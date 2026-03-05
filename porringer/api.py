"""API for Porringer"""

import asyncio
import logging
import warnings

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.backend.command.core.execution import execute_package, execute_uninstall
from porringer.backend.command.core.resolution import ResolutionContext, resolve_operation, resolve_uninstall_operation, resolved_to_result
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
    SyncStrategy,
)
from porringer.utility.download import download_file

logger = logging.getLogger(__name__)


class API:
    """API for programmatic access to Porringer's functionality.

    Provides namespace sub-APIs:

    * ``api.plugin`` — plugin listing, package queries (including
      per-runtime queries via ``list_packages_by_runtime``),
      install/uninstall.
    * ``api.sync``   — manifest loading, streaming execution, update
      checks (including per-runtime checks via
      ``check_updates_by_runtime``).
    * ``api.cache``  — directory registration and validation.

    Cross-cutting helpers live directly on the ``API`` class:
    :meth:`discover_plugins`, :meth:`upgrade`, :meth:`uninstall`,
    :meth:`download`, :meth:`check_self_updates`.
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

        # Cache manager for directory storage
        self.cache = DirectoryCacheManager(configuration.data_directory)

        self.plugin = PluginCommands()
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
        forwarded to every subsequent operation (``execute_stream``,
        ``load_manifest``, ``list``, ``list_packages``, ``uninstall``,
        etc.) so that plugin discovery and runtime resolution happen
        exactly once.

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
    async def resolve_runtime_context(
        environments: dict[str, Environment] | None = None,
    ) -> RuntimeContext:
        """Resolve a :class:`RuntimeContext` from available RuntimeProviders.

        .. deprecated::
            Use :meth:`discover_plugins` instead — it resolves the
            runtime context as part of plugin discovery and attaches
            it to the returned ``DiscoveredPlugins.runtime_context``.

        Args:
            environments: Optional pre-built environment dict.

        Returns:
            A :class:`RuntimeContext` with resolved interpreter paths
            (may be empty when no RuntimeProvider is available).
        """
        warnings.warn(
            'API.resolve_runtime_context() is deprecated. '
            'Use API.discover_plugins() and access plugins.runtime_context instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        if environments is None:
            plugins = await API.discover_plugins(resolve_runtime=True)
            return plugins.runtime_context  # type: ignore[return-value]
        return await Builder.resolve_runtime_context(environments)

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

    @staticmethod
    async def upgrade(
        plugin_name: str,
        package: PackageRef,
        *,
        runtime_tag: str | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
        dry_run: bool = False,
    ) -> SetupActionResult:
        """Upgrade (or install) a single package to its latest version.

        Resolves the named plugin, checks whether the package is
        already installed, and either upgrades it to the latest
        allowed version or installs it if absent.  When ``dry_run``
        is ``True``, only reports what *would* happen without
        executing.

        This is an imperative operation that operates outside the
        manifest-driven sync flow.  It is intended for GUI clients
        that wish to offer per-package upgrades, optionally scoped
        to a specific runtime via *runtime_tag*.

        Args:
            plugin_name: The installer plugin name (e.g. ``"pipx"``,
                ``"uv"``, ``"npm"``).
            package: The package to upgrade.
            runtime_tag: Optional runtime tag (e.g. ``"3.12"``) to
                target a specific interpreter.  When provided, the
                action's ``runtime_tag`` field is set and the
                execution engine resolves the corresponding
                interpreter path.
            plugins: Pre-discovered plugins from :meth:`discover_plugins`.
                When provided, plugin discovery is skipped and
                ``runtime_context`` is extracted from
                ``plugins.runtime_context`` if not explicitly supplied.
            runtime_context: Optional resolved runtime paths.  When
                provided, Python-ecosystem plugins use this to target
                the correct interpreter instead of ``sys.executable``.
                Overrides ``plugins.runtime_context`` when both are
                given.
            dry_run: When ``True``, resolve presence but do not execute.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        logger.debug(
            'upgrade requested: plugin=%s package=%s runtime_tag=%s dry_run=%s',
            plugin_name,
            package.name,
            runtime_tag,
            dry_run,
        )

        if plugins is None:
            plugins = await API.discover_plugins(use_cache=True, resolve_runtime=(runtime_context is None))

        environments = plugins.environments

        # Resolve runtime context: explicit > plugins.runtime_context
        if runtime_context is None:
            runtime_context = plugins.runtime_context

        if plugin_name not in environments:
            logger.warning("Plugin '%s' is not available for upgrade of '%s'", plugin_name, package.name)
            return SetupActionResult(
                action=SetupAction(description=f"Upgrade '{package.name}' via {plugin_name}"),
                success=False,
                message=f"Plugin '{plugin_name}' is not available",
            )

        environment = environments[plugin_name]
        action = SetupAction(
            description=f"Upgrade '{package.name}' via {plugin_name}",
            kind=environment.plugin_kind(),
            ecosystem=environment.ecosystem(),
            installer=plugin_name,
            package=package,
            runtime_tag=runtime_tag,
        )

        ctx = ResolutionContext(runtime_context=runtime_context)

        if dry_run:
            resolved = await resolve_operation(action, environments, SyncStrategy.LATEST, ctx)
            return resolved_to_result(resolved)

        event_queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        result = await execute_package(action, environments, SyncStrategy.LATEST, event_queue, context=ctx)
        logger.info(
            'upgrade result: success=%s skipped=%s skip_reason=%s message=%s',
            result.success,
            result.skipped,
            result.skip_reason,
            result.message,
        )
        return result

    @staticmethod
    async def uninstall(
        plugin_name: str,
        package: PackageRef,
        *,
        plugins: DiscoveredPlugins | None = None,
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
            plugins: Pre-discovered plugins from :meth:`discover_plugins`.
                When provided, plugin discovery is skipped and
                ``runtime_context`` is extracted from
                ``plugins.runtime_context`` if not explicitly supplied.
            runtime_context: Optional resolved runtime paths.  When
                provided, Python-ecosystem plugins use this to target
                the correct interpreter instead of ``sys.executable``.
                Overrides ``plugins.runtime_context`` when both are
                given.
            dry_run: When ``True``, resolve presence but do not execute.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        logger.debug('uninstall requested: plugin=%s package=%s dry_run=%s', plugin_name, package.name, dry_run)

        if plugins is None:
            plugins = await API.discover_plugins(use_cache=True, resolve_runtime=(runtime_context is None))

        environments = plugins.environments

        # Resolve runtime context: explicit > plugins.runtime_context
        if runtime_context is None:
            runtime_context = plugins.runtime_context

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
