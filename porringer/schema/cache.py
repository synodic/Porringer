"""Data models and schemas for cache.

Directory cache schemas.
"""

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir
from pydantic import Field

from porringer.core.schema import PorringerModel


class LocalConfiguration(PorringerModel):
    """Configuration provided by the application running Porringer."""

    cache_directory: Path = Field(
        default=Path(user_cache_dir('porringer', 'synodic')), description='The application cache path '
    )


class ManifestDirectory(PorringerModel):
    """A directory or file path referencing a manifest.

    The path may point to a directory containing any recognised
    manifest file (see ``manifest_filenames()``), or directly to a
    manifest file.  When the path is a file, the sync engine uses
    the file's parent directory as the starting point for
    project-root discovery.
    """

    path: Path = Field(description='Absolute path to a directory or manifest file')
    name: str | None = Field(default=None, description='Optional display name/alias')


class DirectoryCache(PorringerModel):
    """Persisted cache of manifest directories."""

    version: str = Field(default='1', description='Cache schema version')
    directories: list[ManifestDirectory] = Field(default_factory=list, description='Registered directories')


@dataclass(slots=True)
class DirectoryValidationResult:
    """Result of listing/validating a single cached directory entry.

    Returned by ``DirectoryCacheManager.list_directories()`` for
    **every** registered directory.

    Args:
        directory: The cached directory entry.
        exists: Whether the path exists on disk.  ``None`` when
            validation was not requested.
        has_manifest: Whether a valid manifest was found at the path.
            ``None`` when ``exists`` is ``False``, validation was not
            requested, or manifest checking was not requested.
    """

    directory: ManifestDirectory
    exists: bool | None = None
    has_manifest: bool | None = None
