"""Download utilities with hash verification and progress reporting."""

import asyncio
import contextlib
import hashlib
import http.client
import tempfile
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any, BinaryIO
from urllib.request import Request, urlopen

import httpx

from porringer.schema import (
    CancellationToken,
    DownloadParameters,
    DownloadResult,
    HashAlgorithm,
    ProgressCallback,
)


@dataclass
class _DownloadState:
    """State for download operation.

    Args:
        parameters: Download parameters.
        logger: Logger instance.
        expected_algorithm: Hash algorithm if verifying.
        expected_digest: Expected hash digest if verifying.
        progress_callback: Optional progress callback.
    """

    parameters: DownloadParameters
    logger: Logger
    expected_algorithm: HashAlgorithm | None
    expected_digest: str | None
    progress_callback: ProgressCallback | None


def parse_hash_string(hash_string: str) -> tuple[HashAlgorithm, str]:
    """Parses a hash string in "algorithm:hexdigest" format.

    Args:
        hash_string: Hash string like "sha256:abc123..."

    Returns:
        Tuple of (algorithm, hexdigest).

    Raises:
        ValueError: If the format is invalid or algorithm unsupported.
    """
    if ':' not in hash_string:
        raise ValueError(f"Invalid hash format: {hash_string}. Expected 'algorithm:hexdigest'")

    algorithm_str, hexdigest = hash_string.split(':', 1)
    algorithm_str = algorithm_str.lower()

    try:
        algorithm = HashAlgorithm(algorithm_str)
    except ValueError:
        supported = ', '.join(a.value for a in HashAlgorithm)
        raise ValueError(f'Unsupported hash algorithm: {algorithm_str}. Supported: {supported}') from None

    return algorithm, hexdigest


def compute_file_hash(path: Path, algorithm: HashAlgorithm, chunk_size: int = 8192) -> str:
    """Computes the hash of a file.

    Args:
        path: Path to the file.
        algorithm: Hash algorithm to use.
        chunk_size: Size of chunks to read.

    Returns:
        Hexadecimal hash digest.
    """
    hasher = hashlib.new(algorithm.value)

    with open(path, 'rb') as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)

    return hasher.hexdigest()


def download_file(
    parameters: DownloadParameters,
    logger: Logger,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Downloads a file with optional hash verification.

    Downloads to a temporary file first, verifies hash if provided,
    then atomically moves to the destination.

    Args:
        parameters: Download parameters.
        logger: Logger instance.
        progress_callback: Optional callback for progress updates.

    Returns:
        DownloadResult with success status and details.
    """
    logger.info(f'Downloading: {parameters.url}')

    # Parse and validate hash if provided
    try:
        expected_algorithm, expected_digest = _parse_and_validate_hash(parameters.expected_hash)
    except ValueError as e:
        return DownloadResult(success=False, message=str(e))

    # Create parent directory if needed
    parameters.destination.parent.mkdir(parents=True, exist_ok=True)

    # Download to temp file (atomic write pattern)
    return _download_with_temp_file(parameters, logger, expected_algorithm, expected_digest, progress_callback)


def _parse_and_validate_hash(
    expected_hash: str | None,
) -> tuple[HashAlgorithm | None, str | None]:
    """Parse and validate hash string.

    Args:
        expected_hash: Hash string in "algorithm:digest" format.

    Returns:
        Tuple of (algorithm, digest) or (None, None) if no hash provided.

    Raises:
        ValueError: If hash format is invalid.
    """
    if not expected_hash:
        return None, None

    return parse_hash_string(expected_hash)


def _download_with_temp_file(
    parameters: DownloadParameters,
    logger: Logger,
    expected_algorithm: HashAlgorithm | None,
    expected_digest: str | None,
    progress_callback: ProgressCallback | None,
) -> DownloadResult:
    """Download file to temporary location with verification.

    Args:
        parameters: Download parameters.
        logger: Logger instance.
        expected_algorithm: Hash algorithm for verification.
        expected_digest: Expected hash digest.
        progress_callback: Optional progress callback.

    Returns:
        DownloadResult.
    """
    state = _DownloadState(parameters, logger, expected_algorithm, expected_digest, progress_callback)
    temp_path: Path | None = None

    try:
        # Create temp file
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=parameters.destination.parent,
            prefix='.download_',
            suffix='.tmp',
        )
        temp_path = Path(temp_path_str)

        # Perform download
        result = _perform_download(temp_fd, state)

        if result.success:
            # Atomic move to destination
            temp_path.replace(parameters.destination)
            logger.info(f'Saved to: {parameters.destination}')

        return result

    except TimeoutError:
        return DownloadResult(success=False, message=f'Download timed out after {parameters.timeout} seconds')
    except Exception as e:
        logger.error(f'Download failed: {e}')
        return DownloadResult(success=False, message=str(e))
    finally:
        # Clean up temp file on failure
        if temp_path and temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()


def _perform_download(temp_fd: int, state: _DownloadState) -> DownloadResult:
    """Perform the actual download and verification.

    Args:
        temp_fd: File descriptor for temp file.
        state: Download state.

    Returns:
        DownloadResult.
    """
    request = Request(state.parameters.url)
    request.add_header('User-Agent', 'porringer/1.0')

    downloaded = 0
    hasher: Any = None
    if state.expected_algorithm:
        hasher = hashlib.new(state.expected_algorithm.value)

    with urlopen(request, timeout=state.parameters.timeout) as response:
        total_size = response.headers.get('Content-Length')
        total_size = int(total_size) if total_size else None

        # Validate size from headers if available
        size_check = _validate_content_length(state.parameters.expected_size, total_size)
        if not size_check[0]:
            return DownloadResult(success=False, message=size_check[1])

        with open(temp_fd, 'wb') as f:
            downloaded = _write_file_chunks(f, response, state, hasher)

    state.logger.info(f'Downloaded {downloaded} bytes')

    # Verify hash and size
    result = _verify_download(downloaded, state.expected_digest, hasher, state.parameters, state.logger)

    if result:
        return result

    return DownloadResult(success=True, path=state.parameters.destination, verified=bool(hasher), size=downloaded)


def _validate_content_length(expected_size: int | None, content_length: int | None) -> tuple[bool, str | None]:
    """Validate content length matches expected size.

    Args:
        expected_size: Expected size or None.
        content_length: Content-Length header or None.

    Returns:
        (True, None) if valid or (False, error_message).
    """
    if expected_size and content_length and content_length != expected_size:
        return (False, f'Size mismatch: expected {expected_size}, got {content_length}')

    return (True, None)


def _write_file_chunks(
    file: BinaryIO,
    response: http.client.HTTPResponse,
    state: _DownloadState,
    hasher: Any,
) -> int:
    """Write response chunks to file.

    Args:
        file: Open file object.
        response: URL response.
        state: Download state.
        hasher: Hash object or None.

    Returns:
        Total bytes downloaded.
    """
    downloaded = 0
    header_size = response.headers.get('Content-Length')
    total_size: int | None = int(header_size) if header_size else None

    while True:
        chunk = response.read(state.parameters.chunk_size)
        if not chunk:
            break
        file.write(chunk)
        downloaded += len(chunk)

        if hasher:
            hasher.update(chunk)

        if state.progress_callback:
            state.progress_callback(downloaded, total_size)

    return downloaded


def _verify_download(
    downloaded: int,
    expected_digest: str | None,
    hasher: Any,
    parameters: DownloadParameters,
    logger: Logger,
) -> DownloadResult | None:
    """Verify downloaded file hash and size.

    Args:
        downloaded: Bytes downloaded.
        expected_digest: Expected hash digest.
        hasher: Hash object or None.
        parameters: Download parameters.
        logger: Logger instance.

    Returns:
        DownloadResult on verification failure, None on success.
    """
    # Verify hash
    if hasher and expected_digest:
        actual_digest = hasher.hexdigest()
        if actual_digest.lower() != expected_digest.lower():
            return DownloadResult(
                success=False,
                message=f'Hash mismatch: expected {expected_digest}, got {actual_digest}',
            )
        logger.info('Hash verified')

    # Verify size
    if parameters.expected_size and downloaded != parameters.expected_size:
        return DownloadResult(
            success=False,
            message=f'Size mismatch: expected {parameters.expected_size}, got {downloaded}',
        )

    return None


# --- Async Download Implementation ---


async def async_download_file(
    parameters: DownloadParameters,
    logger: Logger,
    progress_callback: ProgressCallback | None = None,
    cancellation_token: CancellationToken | None = None,
) -> DownloadResult:
    """Asynchronously downloads a file with optional hash verification.

    Uses httpx for non-blocking HTTP requests. Suitable for GUI applications
    that need to keep their event loop responsive during downloads.

    Downloads to a temporary file first, verifies hash if provided,
    then atomically moves to the destination.

    Note: Callbacks are invoked from the asyncio event loop thread.
    GUI applications must marshal updates to their UI thread.

    Args:
        parameters: Download parameters.
        logger: Logger instance.
        progress_callback: Optional callback for progress updates (downloaded, total).
        cancellation_token: Optional token for cooperative cancellation.

    Returns:
        DownloadResult with success status and details.

    Raises:
        asyncio.CancelledError: If cancellation_token is cancelled.
    """
    logger.info(f'Downloading (async): {parameters.url}')

    # Check for cancellation before starting
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()

    # Parse and validate hash if provided
    try:
        expected_algorithm, expected_digest = _parse_and_validate_hash(parameters.expected_hash)
    except ValueError as e:
        return DownloadResult(success=False, message=str(e))

    # Create parent directory if needed
    parameters.destination.parent.mkdir(parents=True, exist_ok=True)

    # Bundle state for download
    state = _DownloadState(
        parameters=parameters,
        logger=logger,
        expected_algorithm=expected_algorithm,
        expected_digest=expected_digest,
        progress_callback=progress_callback,
    )

    # Download to temp file
    temp_path: Path | None = None

    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=parameters.destination.parent,
            prefix='.download_',
            suffix='.tmp',
        )
        temp_path = Path(temp_path_str)

        result = await _async_perform_download(
            temp_fd,
            state,
            cancellation_token,
        )

        if result.success:
            temp_path.replace(parameters.destination)
            logger.info(f'Saved to: {parameters.destination}')
            # Update path in result
            return DownloadResult(
                success=True,
                path=parameters.destination,
                verified=result.verified,
                size=result.size,
            )

        return result

    except asyncio.CancelledError:
        logger.info('Download cancelled')
        raise
    except TimeoutError:
        return DownloadResult(success=False, message=f'Download timed out after {parameters.timeout} seconds')
    except httpx.HTTPError as e:
        logger.error(f'HTTP error: {e}')
        return DownloadResult(success=False, message=str(e))
    except Exception as e:
        logger.error(f'Download failed: {e}')
        return DownloadResult(success=False, message=str(e))
    finally:
        if temp_path and temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()


async def _async_stream_download(
    temp_fd: int,
    parameters: DownloadParameters,
    *,
    hasher: Any,
    progress_callback: ProgressCallback | None,
    cancellation_token: CancellationToken | None,
) -> int:
    """Stream download chunks to file.

    Returns:
        Number of bytes downloaded.

    Raises:
        DownloadSizeMismatchError: If content-length doesn't match expected size.
    """
    downloaded = 0

    async with (
        httpx.AsyncClient(follow_redirects=True) as client,
        client.stream('GET', parameters.url) as response,
    ):
        response.raise_for_status()

        total_size: int | None = None
        content_length = response.headers.get('Content-Length')
        if content_length:
            total_size = int(content_length)

        # Validate size from headers
        if parameters.expected_size and total_size and total_size != parameters.expected_size:
            raise DownloadSizeMismatchError(parameters.expected_size, total_size)

        with open(temp_fd, 'wb') as f:
            async for chunk in response.aiter_bytes(chunk_size=parameters.chunk_size):
                # Check for cancellation between chunks
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()

                f.write(chunk)
                downloaded += len(chunk)

                if hasher:
                    hasher.update(chunk)

                if progress_callback:
                    progress_callback(downloaded, total_size)

    return downloaded


class DownloadSizeMismatchError(Exception):
    """Size mismatch during download."""

    def __init__(self, expected: int, actual: int) -> None:
        """Initialize with expected and actual sizes."""
        self.expected = expected
        self.actual = actual
        super().__init__(f'Size mismatch: expected {expected}, got {actual}')


async def _async_perform_download(
    temp_fd: int,
    state: _DownloadState,
    cancellation_token: CancellationToken | None,
) -> DownloadResult:
    """Perform async download with httpx.

    Args:
        temp_fd: File descriptor for temp file.
        state: Download state with parameters and callbacks.
        cancellation_token: Optional cancellation token.

    Returns:
        DownloadResult.
    """
    hasher: Any = None
    if state.expected_algorithm:
        hasher = hashlib.new(state.expected_algorithm.value)

    downloaded = 0

    try:
        async with asyncio.timeout(state.parameters.timeout):
            downloaded = await _async_stream_download(
                temp_fd,
                state.parameters,
                hasher=hasher,
                progress_callback=state.progress_callback,
                cancellation_token=cancellation_token,
            )
    except DownloadSizeMismatchError as e:
        return DownloadResult(success=False, message=str(e))

    state.logger.info(f'Downloaded {downloaded} bytes')

    # Verify hash and size (reuse sync verification logic)
    error = _verify_download(downloaded, state.expected_digest, hasher, state.parameters, state.logger)
    if error:
        return error

    return DownloadResult(success=True, path=state.parameters.destination, verified=bool(hasher), size=downloaded)
