"""Data models and schemas for download.

Download schemas.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import Field

from porringer.core.schema import PorringerModel


class HashAlgorithm(Enum):
    """Supported hash algorithms for verification."""

    SHA256 = 'sha256'
    SHA512 = 'sha512'


class DownloadParameters(PorringerModel):
    """Parameters for downloading files."""

    url: str = Field(description='URL to download')
    destination: Path = Field(description='Destination file path')
    expected_hash: str | None = Field(
        default=None, description='Expected hash in "algorithm:hexdigest" format (e.g., "sha256:abc123...")'
    )
    expected_size: int | None = Field(default=None, description='Expected file size in bytes')
    timeout: int = Field(default=300, description='Download timeout in seconds')
    chunk_size: int = Field(default=8192, description='Download chunk size in bytes')


# Type alias for progress callback: (downloaded_bytes, total_bytes) -> None
ProgressCallback = Callable[[int, int | None], None]


@dataclass(slots=True)
class DownloadResult:
    """Result of a download operation.

    Args:
        success: Whether the download succeeded.
        path: Path to the downloaded file.
        verified: Whether the hash was verified.
        size: Size of the downloaded file in bytes.
        message: Optional message (error details on failure).
    """

    success: bool
    path: Path | None = None
    verified: bool = False
    size: int = 0
    message: str | None = None
