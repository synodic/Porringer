"""API for Porringer"""

import asyncio
import logging

from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import discover_all_plugins
from porringer.backend.command.core.execution import execute_uninstall
from porringer.backend.command.core.resolution import resolve_uninstall_operation, resolved_to_result
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import check_self_updates
from porringer.backend.command.sync import SyncCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import GlobalConfiguration
from porringer.core.schema import PackageRef
from porringer.schema import (
    DownloadParameters,
    DownloadResult,
    LocalConfiguration,
    PackageUpdateInfo,
    ProgressCallback,
    SetupAction,
    SetupActionResult,
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
    async def check_self_updates() -> PackageUpdateInfo:
        """Check for updates to the Porringer package by querying PyPI.

        Returns:
            PackageUpdateInfo with current version, latest version, and update status.
        """
        return await check_self_updates()

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

    @staticmethod
    def uninstall(
        plugin_name: str,
        package: PackageRef,
        *,
        dry_run: bool = False,
    ) -> SetupActionResult:
        """Uninstall a globally-installed package.

        Resolves the named plugin, checks whether the package is
        installed, and runs the plugin's ``async_uninstall`` command.
        When ``dry_run`` is ``True``, only reports whether the
        removal *would* proceed without actually executing it.

        This is an imperative operation that operates outside the
        manifest-driven sync flow.  It is intended for GUI clients
        that wish to offer per-package removal.

        Args:
            plugin_name: The installer plugin name (e.g. ``"pipx"``,
                ``"uv"``, ``"npm"``).
            package: The package to uninstall (only ``name`` is used).
            dry_run: When ``True``, resolve presence but do not execute.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        logger.debug('uninstall requested: plugin=%s package=%s dry_run=%s', plugin_name, package.name, dry_run)

        plugins = discover_all_plugins(use_cache=True)
        environments = plugins.environments

        if plugin_name not in environments:
            logger.warning("Plugin '%s' is not available for uninstall of '%s'", plugin_name, package.name)
            return SetupActionResult(
                action=SetupAction(description=f"Uninstall '{package.name}' via {plugin_name}"),
                success=False,
                message=f"Plugin '{plugin_name}' is not available",
            )

        environment = environments[plugin_name]
        action = SetupAction(
            description=f"Uninstall '{package.name}' via {plugin_name}",
            kind=environment.plugin_kind(),
            ecosystem=environment.ecosystem(),
            installer=plugin_name,
            package=package,
        )

        if dry_run:
            resolved = asyncio.run(resolve_uninstall_operation(action, environments))
            return resolved_to_result(resolved)

        result = asyncio.run(execute_uninstall(action, environments))
        logger.info(
            'uninstall result: success=%s skipped=%s skip_reason=%s message=%s',
            result.success,
            result.skipped,
            result.skip_reason,
            result.message,
        )
        return result
