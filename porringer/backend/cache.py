"""Directory cache management for manifest directories."""

from logging import Logger
from pathlib import Path

from porringer.schema import DirectoryCache, ManifestDirectory


class DirectoryCacheManager:
    """Manages persistent storage of manifest directories.

    Provides CRUD operations for directories with persistence
    to a JSON file in the user's data directory.
    """

    CACHE_FILENAME = 'directories.json'

    def __init__(self, data_directory: Path, logger: Logger) -> None:
        """Initialize the cache manager.

        Args:
            data_directory: The directory where cache will be stored.
            logger: Logger instance for logging actions.
        """
        self.data_directory = data_directory
        self.logger = logger
        self._cache_path = data_directory / self.CACHE_FILENAME
        self._cache: DirectoryCache | None = None

    @property
    def cache_path(self) -> Path:
        """The path to the cache file."""
        return self._cache_path

    def _load(self) -> DirectoryCache:
        """Load cache from disk, creating if necessary.

        Returns:
            The loaded or newly created cache.
        """
        if self._cache is not None:
            return self._cache

        if self._cache_path.exists():
            try:
                self._cache = DirectoryCache.model_validate_json(self._cache_path.read_text(encoding='utf-8'))
                self.logger.debug(f'Loaded directory cache from {self._cache_path}')
            except Exception as e:
                self.logger.warning(f'Failed to load directory cache, creating new: {e}')
                self._cache = DirectoryCache()
        else:
            self._cache = DirectoryCache()

        return self._cache

    def _save(self) -> None:
        """Save cache to disk atomically."""
        if self._cache is None:
            return

        self.data_directory.mkdir(parents=True, exist_ok=True)

        # Write to temp file then rename for atomic write
        temp_path = self._cache_path.with_suffix('.tmp')
        try:
            temp_path.write_text(self._cache.model_dump_json(indent=2), encoding='utf-8')
            temp_path.replace(self._cache_path)
            self.logger.debug(f'Saved directory cache to {self._cache_path}')
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    @staticmethod
    def _normalize_path(path: Path) -> Path:
        """Normalize a path for consistent storage.

        Args:
            path: The path to normalize.

        Returns:
            The normalized absolute path.
        """
        return path.resolve()

    # --- Directory Operations ---

    def add_directory(self, path: Path, name: str | None = None, validate: bool = True) -> ManifestDirectory:
        """Add a directory to the cache.

        Args:
            path: Path to the directory.
            name: Optional display name for the directory.
            validate: If True, validate path exists.

        Returns:
            The added ManifestDirectory.

        Raises:
            ValueError: If path doesn't exist (when validate=True) or is already registered.
        """
        cache = self._load()
        normalized = DirectoryCacheManager._normalize_path(path)

        if validate and not normalized.exists():
            raise ValueError(f'Path does not exist: {normalized}')

        # Check for duplicates
        for existing in cache.directories:
            if DirectoryCacheManager._normalize_path(existing.path) == normalized:
                raise ValueError(f'Directory already registered: {normalized}')

        directory = ManifestDirectory(path=normalized, name=name)
        cache.directories.append(directory)
        self._save()

        self.logger.info(f'Added directory: {normalized}')
        return directory

    def remove_directory(self, path: Path) -> bool:
        """Remove a directory from the cache.

        Args:
            path: Path to remove.

        Returns:
            True if removed, False if not found.
        """
        cache = self._load()
        normalized = DirectoryCacheManager._normalize_path(path)

        for i, directory in enumerate(cache.directories):
            if DirectoryCacheManager._normalize_path(directory.path) == normalized:
                cache.directories.pop(i)
                self._save()
                self.logger.info(f'Removed directory: {normalized}')
                return True

        return False

    def list_directories(self) -> list[ManifestDirectory]:
        """List all registered directories.

        Returns:
            List of registered directories.
        """
        cache = self._load()
        return list(cache.directories)

    def get_paths(self) -> list[Path]:
        """Get all registered paths.

        Returns:
            List of paths.
        """
        return [d.path for d in self.list_directories()]

    def update_directory(self, path: Path, name: str | None = None) -> ManifestDirectory | None:
        """Update a directory's metadata.

        Args:
            path: Path of the directory to update.
            name: New name (None to keep existing).

        Returns:
            Updated directory or None if not found.
        """
        cache = self._load()
        normalized = DirectoryCacheManager._normalize_path(path)

        for directory in cache.directories:
            if DirectoryCacheManager._normalize_path(directory.path) == normalized:
                if name is not None:
                    directory.name = name
                self._save()
                return directory

        return None

    # --- Validation ---

    def validate_directories(self) -> list[tuple[ManifestDirectory, str]]:
        """Validate all directories exist.

        Returns:
            List of (directory, error_message) for invalid directories.
        """
        invalid: list[tuple[ManifestDirectory, str]] = []
        for directory in self.list_directories():
            if not directory.path.exists():
                invalid.append((directory, f'Path does not exist: {directory.path}'))
            elif not directory.path.is_dir():
                invalid.append((directory, f'Path is not a directory: {directory.path}'))
        return invalid

    def clear(self) -> None:
        """Clear all directories from the cache."""
        self._cache = DirectoryCache()
        self._save()
        self.logger.info('Cleared directory cache')
