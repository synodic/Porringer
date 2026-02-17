"""API for Porringer"""

import logging

from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import check_for_updates
from porringer.backend.command.sync import SyncCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import GlobalConfiguration
from porringer.schema import (
    DownloadParameters,
    DownloadResult,
    LocalConfiguration,
    PackageUpdateInfo,
    ProgressCallback,
)
from porringer.utility.download import download_file

logger = logging.getLogger(__name__)


class API:
    """API for programmatic access to Porringer's functionality."""

    def __init__(
        self,
        local_configuration: LocalConfiguration,
        global_configuration: GlobalConfiguration | None = None,
    ) -> None:
        """Initializes the API

        Args:
            local_configuration: The local configuration.
            global_configuration: Optional global configuration (uses defaults if not provided).
        """
        if global_configuration is None:
            global_configuration = GlobalConfiguration()

        configuration = resolve_configuration(local_configuration, global_configuration)

        # Cache manager for directory storage
        self.cache = DirectoryCacheManager(configuration.data_directory)

        self.plugin = PluginCommands()
        self.sync = SyncCommands(self.cache)

    @staticmethod
    async def check_updates() -> PackageUpdateInfo:
        """Check for updates to the Porringer package by querying PyPI.

        Returns:
            PackageUpdateInfo with current version, latest version, and update status.
        """
        return await check_for_updates()

    @staticmethod
    def download(
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
        logger.info(f'Downloading: {parameters.url}')
        return download_file(parameters, progress_callback)
