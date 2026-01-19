"""The update command module."""

from logging import Logger

from porringer.backend.update_sources import UpdateSourceAdapter
from porringer.backend.update_sources.custom import CustomURLAdapter
from porringer.backend.update_sources.github import GitHubAdapter
from porringer.backend.update_sources.pypi import PyPIAdapter
from porringer.schema import (
    CheckUpdateParameters,
    DownloadParameters,
    DownloadResult,
    ProgressCallback,
    UpdateInfo,
    UpdateSource,
)
from porringer.utility.download import download_file
from porringer.utility.exception import UpdateError


class UpdateCommands:
    """Update commands for checking and downloading updates."""

    def __init__(self, logger: Logger) -> None:
        """Initialize the UpdateCommands class.

        Args:
            logger: Logger instance for logging actions.
        """
        self.logger = logger

    def _get_adapter(self, source: UpdateSource) -> UpdateSourceAdapter:
        """Gets the appropriate adapter for the update source.

        Args:
            source: The update source type.

        Returns:
            The adapter instance.
        """
        match source:
            case UpdateSource.GITHUB_RELEASES:
                return GitHubAdapter(self.logger)
            case UpdateSource.PYPI:
                return PyPIAdapter(self.logger)
            case UpdateSource.CUSTOM_URL:
                return CustomURLAdapter(self.logger)
            case _:
                msg = f'Unknown update source: {source}'
                raise UpdateError(msg)

    def check(self, parameters: CheckUpdateParameters) -> UpdateInfo:
        """Check for available updates.

        Args:
            parameters: The check parameters including source and current version.

        Returns:
            UpdateInfo with version comparison and download information.

        Raises:
            UpdateError: If the check fails.
        """
        self.logger.info(f'Checking for updates via {parameters.source.name}')

        adapter = self._get_adapter(parameters.source)
        return adapter.check(parameters)

    def download(
        self,
        parameters: DownloadParameters,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download a file with optional hash verification.

        Args:
            parameters: Download parameters including URL and destination.
            progress_callback: Optional callback for progress updates.

        Returns:
            DownloadResult with success status and details.
        """
        self.logger.info(f'Downloading: {parameters.url}')

        return download_file(parameters, self.logger, progress_callback)
