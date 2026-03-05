"""API for Porringer"""

import asyncio
import logging
from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.backend.command.package import PackageCommands
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import check_self_updates
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
    """API for programmatic access to Porringer's functionality.

    Provides namespace sub-APIs:

    * ``api.plugin``  — porringer extension management (install,
      update, remove extension packages).
    * ``api.package`` — managed-package operations (list, install,
      upgrade, uninstall packages, check for updates).
    * ``api.sync``    — manifest loading, streaming execution.
    * ``api.cache``   — directory registration and validation.

    Cross-cutting helpers live directly on the ``API`` class:
    :meth:`discover_plugins`, :meth:`check_self_updates`,
    :meth:`download`.
    """

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

        self.cache = DirectoryCacheManager(configuration.data_directory)

        self.plugin = PluginCommands()
        self.package = PackageCommands()
        self.sync = SyncCommands(self.cache)

    # --- Discovery & runtime resolution ---

    @staticmethod
    async def discover_plugins(
        *,
        use_cache: bool = True,
        resolve_runtime: bool = True,
    ) -> DiscoveredPlugins:
        """Discover all plugins and optionally resolve runtime context.

        This is the recommended entry-point for GUI callers.  It
        returns a :class:`DiscoveredPlugins` object that can be
        forwarded to every subsequent operation
        (``api.sync.execute_stream``, ``api.package.list``,
        ``api.package.upgrade``, etc.) so that plugin discovery and
        runtime resolution happen exactly once.

        Args:
            use_cache: Reuse cached entry-point scan metadata when
                ``True`` (the default).  Pass ``False`` to force a
                fresh scan after installing/removing plugin packages.
            resolve_runtime: When ``True`` (the default), resolve a
                :class:`RuntimeContext` from available
                ``RuntimeProvider`` plugins and attach it to the
                returned object as ``plugins.runtime_context``.

        Returns:
            A :class:`DiscoveredPlugins` carrying environment,
            project-environment, and SCM plugins, plus an optional
            :class:`RuntimeContext`.
        """
        plugins = await asyncio.to_thread(discover_all_plugins, use_cache=use_cache)
        if resolve_runtime:
            plugins.runtime_context = await Builder.resolve_runtime_context(plugins.environments)
            logger.debug(
                'discover_plugins: runtime_context=%s',
                {k: str(v) for k, v in plugins.runtime_context.executables.items()}
                if plugins.runtime_context.executables
                else '<empty>',
            )
        return plugins

    @staticmethod
    async def check_self_updates() -> PackageUpdateInfo:
        """Check for updates to the Porringer package by querying PyPI.

        Returns:
            PackageUpdateInfo with current version, latest version, and update status.
        """
        return await check_self_updates()

    @staticmethod
    async def download(
        parameters: DownloadParameters,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download a file with optional hash verification.

        Uses httpx for non-blocking HTTP requests.  Suitable for GUI
        applications that need to keep their event loop responsive
        during downloads.

        Args:
            parameters: Download parameters including URL and destination.
            progress_callback: Optional callback for progress updates.

        Returns:
            DownloadResult with success status and details.
        """
        logger.info(f'Downloading: {parameters.url}')
        return await download_file(parameters, progress_callback)
