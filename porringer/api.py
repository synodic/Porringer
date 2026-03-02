"""API for Porringer"""

import asyncio
import logging

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import discover_all_plugins
from porringer.backend.command.core.execution import execute_uninstall
from porringer.backend.command.core.resolution import ResolutionContext, resolve_uninstall_operation, resolved_to_result
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import check_self_updates
from porringer.backend.command.sync import SyncCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import GlobalConfiguration
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PackageRef
from porringer.schema import (
    DownloadParameters,
    DownloadResult,
    LocalConfiguration,
    PackageUpdateInfo,
    ProgressCallback,
    ProgressEvent,
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
    async def resolve_runtime_context(
        environments: dict[str, Environment] | None = None,
    ) -> RuntimeContext:
        """Resolve a :class:`RuntimeContext` from available RuntimeProviders.

        This is the recommended entry-point for GUI callers that need
        a ``RuntimeContext`` before issuing ``list``, ``uninstall``,
        or ``check_updates`` calls.  Resolving once and reusing the
        result avoids redundant work.

        When *environments* is ``None`` the current set of discovered
        environment plugins is used automatically.

        Args:
            environments: Optional pre-built environment dict.  Pass
                this when you already have a plugin map to avoid a
                second discovery round.

        Returns:
            A :class:`RuntimeContext` with resolved interpreter paths
            (may be empty when no RuntimeProvider is available).
        """
        if environments is None:
            plugins = discover_all_plugins(use_cache=True)
            environments = plugins.environments
        return await Builder.resolve_runtime_context(environments)

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

    @staticmethod
    async def uninstall(
        plugin_name: str,
        package: PackageRef,
        *,
        runtime_context: RuntimeContext | None = None,
        dry_run: bool = False,
    ) -> SetupActionResult:
        """Uninstall a globally-installed package.

        Resolves the named plugin, checks whether the package is
        installed, and runs the plugin's ``uninstall`` command.
        When ``dry_run`` is ``True``, only reports whether the
        removal *would* proceed without actually executing it.

        This is an imperative operation that operates outside the
        manifest-driven sync flow.  It is intended for GUI clients
        that wish to offer per-package removal.

        Args:
            plugin_name: The installer plugin name (e.g. ``"pipx"``,
                ``"uv"``, ``"npm"``).
            package: The package to uninstall (only ``name`` is used).
            runtime_context: Optional resolved runtime paths.  When
                provided, Python-ecosystem plugins use this to target
                the correct interpreter instead of ``sys.executable``.
            dry_run: When ``True``, resolve presence but do not execute.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        logger.debug('uninstall requested: plugin=%s package=%s dry_run=%s', plugin_name, package.name, dry_run)

        plugins = discover_all_plugins(use_cache=True)
        environments = plugins.environments
        # Cached *scan metadata* is reused; plugin instances are fresh
        # (constructed by the factory inside discover_all_plugins).

        # Auto-resolve runtime context when the caller did not supply one,
        # matching the pattern used by PluginCommands.list_packages().
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)
            logger.debug(
                'uninstall: auto-resolved runtime_context: %s',
                {k: str(v) for k, v in runtime_context.executables.items()} if runtime_context.executables else '<empty>',
            )

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

        ctx = ResolutionContext(runtime_context=runtime_context)

        if dry_run:
            resolved = await resolve_uninstall_operation(action, environments, ctx)
            return resolved_to_result(resolved)

        event_queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        result = await execute_uninstall(action, environments, event_queue, context=ctx)
        logger.info(
            'uninstall result: success=%s skipped=%s skip_reason=%s message=%s',
            result.success,
            result.skipped,
            result.skip_reason,
            result.message,
        )
        return result
