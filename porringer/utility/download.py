"""Download utilities with hash verification and progress reporting."""

import asyncio
import contextlib
import hashlib
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from porringer.schema import (
    CancellationToken,
    DownloadParameters,
    DownloadResult,
    HashAlgorithm,
    ProgressCallback,
)
from porringer.utility import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

# Retry configuration for transient network errors.
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds; doubles each attempt
_SERVER_ERROR_THRESHOLD = 500  # HTTP status codes >= this are retryable


@dataclass(slots=True)
class _DownloadState:
    """State for download operation.

    Args:
        parameters: Download parameters.
        expected_algorithm: Hash algorithm if verifying.
        expected_digest: Expected hash digest if verifying.
        progress_callback: Optional progress callback.
    """

    parameters: DownloadParameters
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


def _verify_download(
    downloaded: int,
    expected_digest: str | None,
    hasher: Any,
    parameters: DownloadParameters,
) -> DownloadResult | None:
    """Verify downloaded file hash and size.

    Args:
        downloaded: Bytes downloaded.
        expected_digest: Expected hash digest.
        hasher: Hash object or None.
        parameters: Download parameters.

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


async def _download_attempt(
    session: aiohttp.ClientSession,
    state: _DownloadState,
    cancellation_token: CancellationToken | None,
) -> DownloadResult:
    """Execute a single async download attempt.

    Downloads to a temporary file, verifies, and moves to the
    destination on success.  The caller is responsible for retry logic.

    Args:
        session: Shared aiohttp session.
        state: Download state.
        cancellation_token: Optional cancellation token.

    Returns:
        DownloadResult.

    Raises:
        asyncio.CancelledError: If cancelled.
        TimeoutError: On timeout.
        aiohttp.ClientResponseError: On HTTP status errors.
        aiohttp.ClientError: On other HTTP errors.
        OSError: On filesystem errors.
    """
    temp_path: Path | None = None
    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=state.parameters.destination.parent,
            prefix='.download_',
            suffix='.tmp',
        )
        temp_path = Path(temp_path_str)

        result = await _perform_download(session, temp_fd, state, cancellation_token)

        if result.success:
            temp_path.replace(state.parameters.destination)
            logger.info(f'Saved to: {state.parameters.destination}')
            return DownloadResult(
                success=True,
                path=state.parameters.destination,
                verified=result.verified,
                size=result.size,
            )

        return result
    finally:
        if temp_path and temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()


async def download_file(
    parameters: DownloadParameters,
    progress_callback: ProgressCallback | None = None,
    cancellation_token: CancellationToken | None = None,
) -> DownloadResult:
    """Asynchronously downloads a file with optional hash verification.

    Uses aiohttp for non-blocking HTTP requests. Suitable for GUI applications
    that need to keep their event loop responsive during downloads.

    Downloads to a temporary file first, verifies hash if provided,
    then atomically moves to the destination.  Transient network errors
    are retried up to ``_MAX_RETRIES`` times with exponential back-off.

    Note: Callbacks are invoked from the asyncio event loop thread.
    GUI applications must marshal updates to their UI thread.

    Args:
        parameters: Download parameters.
        progress_callback: Optional callback for progress updates (downloaded, total).
        cancellation_token: Optional token for cooperative cancellation.

    Returns:
        DownloadResult with success status and details.

    Raises:
        asyncio.CancelledError: If cancellation_token is cancelled.
    """
    logger.info(f'Downloading (async): {parameters.url}')

    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()

    try:
        expected_algorithm, expected_digest = _parse_and_validate_hash(parameters.expected_hash)
    except ValueError as e:
        return DownloadResult(success=False, message=str(e))

    parameters.destination.parent.mkdir(parents=True, exist_ok=True)

    state = _DownloadState(
        parameters=parameters,
        expected_algorithm=expected_algorithm,
        expected_digest=expected_digest,
        progress_callback=progress_callback,
    )

    last_result: DownloadResult | None = None

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        for attempt in range(_MAX_RETRIES):
            try:
                result = await _download_attempt(session, state, cancellation_token)
                if not result.success:
                    return result
                return result

            except asyncio.CancelledError:
                logger.info('Download cancelled')
                raise
            except TimeoutError:
                last_result = DownloadResult(
                    success=False, message=f'Download timed out after {parameters.timeout} seconds'
                )
                logger.warning('Timeout on attempt %d/%d', attempt + 1, _MAX_RETRIES)
            except aiohttp.ClientResponseError as e:
                last_result = DownloadResult(success=False, message=str(e))
                if e.status < _SERVER_ERROR_THRESHOLD:
                    logger.error(f'HTTP error: {e}')
                    return last_result
                logger.warning('Retryable HTTP %d on attempt %d/%d', e.status, attempt + 1, _MAX_RETRIES)
            except (aiohttp.ClientError, OSError) as e:
                last_result = DownloadResult(success=False, message=str(e))
                logger.warning('Network error on attempt %d/%d: %s', attempt + 1, _MAX_RETRIES, e)
            except Exception as e:
                logger.error(f'Download failed: {e}')
                return DownloadResult(success=False, message=str(e))

            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.debug('Waiting %.1fs before retry', delay)
                await asyncio.sleep(delay)

    return last_result or DownloadResult(success=False, message='Download failed after retries')


async def _perform_download(
    session: aiohttp.ClientSession,
    temp_fd: int,
    state: _DownloadState,
    cancellation_token: CancellationToken | None,
) -> DownloadResult:
    """Perform async download with aiohttp.

    Args:
        session: Shared aiohttp session.
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
        async with (
            asyncio.timeout(state.parameters.timeout),
            session.get(state.parameters.url, allow_redirects=True) as response,
        ):
            response.raise_for_status()

            total_size: int | None = None
            content_length = response.headers.get('Content-Length')
            if content_length:
                total_size = int(content_length)

            # Validate size from headers
            if state.parameters.expected_size and total_size and total_size != state.parameters.expected_size:
                return DownloadResult(
                    success=False,
                    message=f'Size mismatch: expected {state.parameters.expected_size}, got {total_size}',
                )

            with open(temp_fd, 'wb') as f:
                async for chunk in response.content.iter_chunked(state.parameters.chunk_size):
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled()

                    f.write(chunk)
                    downloaded += len(chunk)

                    if hasher:
                        hasher.update(chunk)

                    if state.progress_callback:
                        state.progress_callback(downloaded, total_size)
    except aiohttp.ClientResponseError:
        raise  # Let the caller's retry logic handle HTTP errors

    logger.info(f'Downloaded {downloaded} bytes')

    # Verify hash and size
    error = _verify_download(downloaded, state.expected_digest, hasher, state.parameters)
    if error:
        return error

    return DownloadResult(success=True, path=state.parameters.destination, verified=bool(hasher), size=downloaded)
