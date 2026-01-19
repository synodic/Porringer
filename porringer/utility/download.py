"""Download utilities with hash verification and progress reporting."""

import contextlib
import hashlib
import http.client
import tempfile
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any, BinaryIO
from urllib.request import Request, urlopen

from porringer.schema import DownloadParameters, DownloadResult, HashAlgorithm, ProgressCallback


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
    hash_result = _parse_and_validate_hash(parameters.expected_hash)

    # Check for error case first (bool, str)
    if not isinstance(hash_result[0], bool):
        # This is (HashAlgorithm | None, str | None)
        expected_algorithm = hash_result[0]
        expected_digest = hash_result[1]
    else:
        # This is (False, error_message)
        return DownloadResult(success=False, message=str(hash_result[1]))

    # Create parent directory if needed
    parameters.destination.parent.mkdir(parents=True, exist_ok=True)

    # Download to temp file (atomic write pattern)
    return _download_with_temp_file(parameters, logger, expected_algorithm, expected_digest, progress_callback)


def _parse_and_validate_hash(
    expected_hash: str | None,
) -> tuple[HashAlgorithm, str] | tuple[None, None] | tuple[bool, str]:
    """Parse and validate hash string.

    Args:
        expected_hash: Hash string in "algorithm:digest" format.

    Returns:
        Tuple of (algorithm, digest), (None, None), or (False, error_message) on error.
    """
    if not expected_hash:
        return None, None

    try:
        algorithm, digest = parse_hash_string(expected_hash)
        return algorithm, digest
    except ValueError as e:
        return False, str(e)


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
