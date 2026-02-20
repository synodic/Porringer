"""Directory cache schemas."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class ManifestDirectory(BaseModel):
    """A directory or file path referencing a manifest.

    The path may point to a directory containing any recognised
    manifest file (see ``manifest_filenames()``), or directly to a
    manifest file.  When the path is a file, the sync engine uses
    the file's parent directory as the starting point for
    project-root discovery.
    """

    path: Path = Field(description='Absolute path to a directory or manifest file')
    name: str | None = Field(default=None, description='Optional display name/alias')


class DirectoryCache(BaseModel):
    """Persisted cache of manifest directories."""

    version: str = Field(default='1', description='Cache schema version')
    directories: list[ManifestDirectory] = Field(default_factory=list, description='Registered directories')


@dataclass
class DirectoryValidationResult:
    """Result of validating a single cached directory entry.

    Returned by ``DirectoryCacheManager.validate_directories()`` for
    **every** registered directory, not just invalid ones.

    Args:
        directory: The cached directory entry.
        exists: Whether the path exists on disk.
        has_manifest: Whether a valid manifest was found at the path.
            ``None`` when ``exists`` is ``False`` or when manifest
            checking was not requested.
    """

    directory: ManifestDirectory
    exists: bool
    has_manifest: bool | None = None
