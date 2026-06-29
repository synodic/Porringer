"""CLI command implementation for package."""

"""The package command module.

Imperative package operations — listing, installing, upgrading,
uninstalling, and checking for updates on packages managed by
environment plugins.

This module complements :mod:`.plugin` (which manages *porringer*
extension packages) and :mod:`.sync` (which handles manifest-driven
execution).
"""

import asyncio
import builtins
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
from packaging.version import Version

from porringer.backend.builder import Builder
from porringer.backend.command.core.action_builder import load_manifest
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins, discover_environments
from porringer.backend.command.core.execution import execute_package, execute_uninstall
from porringer.backend.command.core.resolution import (
    ResolutionContext,
    ResolvedOperation,
    resolve_operation,
    resolve_uninstall_operation,
    resolved_to_result,
)
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.schema import Package, PackageRef
from porringer.schema import (
    CheckParameters,
    CheckResult,
    PackageUpdateInfo,
    ProgressEvent,
    RuntimeCheckResult,
    RuntimePackageResult,
    SetupAction,
    SetupActionResult,
    SyncStrategy,
)
from porringer.schema.plugin import ScopedPackage
from porringer.utility.concurrency import gather_bounded
from porringer.utility.exception import PluginError, UpdateError

logger = logging.getLogger(__name__)

# Default bounded-concurrency limit for fan-out paths whose parameter
# objects do not carry their own ``max_concurrency`` budget.
_DEFAULT_FANOUT = 8

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

type _ResolveFn = Callable[
    [SetupAction, dict[str, Environment], ResolutionContext],
    Awaitable[ResolvedOperation],
]
type _ExecuteFn = Callable[
    [SetupAction, dict[str, Environment], asyncio.Queue[ProgressEvent | None], ResolutionContext],
    Awaitable[SetupActionResult],
]


async def _imperative_action(
    *,
    verb: str,
    plugin_name: str,
    package: PackageRef,
    runtime_tag: str | None,
    plugins: DiscoveredPlugins | None,
    runtime_context: RuntimeContext | None,
    dry_run: bool,
    resolve_fn: _ResolveFn,
    execute_fn: _ExecuteFn,
) -> SetupActionResult:
    """Shared skeleton for imperative package operations.

    Handles plugin discovery, runtime-context resolution, plugin
    validation, action construction, dry-run resolution, and
    execution — the common logic behind :meth:`PackageCommands.upgrade`,
    :meth:`PackageCommands.install`, and :meth:`PackageCommands.uninstall`.
    """
    verb_cap = verb.capitalize()
    logger.debug(
        '%s requested: plugin=%s package=%s runtime_tag=%s dry_run=%s',
        verb,
        plugin_name,
        package.name,
        runtime_tag,
        dry_run,
    )

    if plugins is None:
        plugins = await _discover_with_runtime(runtime_context)

    environments = plugins.environments
    runtime_context = plugins.resolved_runtime(runtime_context)

    if plugin_name not in environments:
        logger.warning("Plugin '%s' is not available for %s of '%s'", plugin_name, verb, package.name)
        return SetupActionResult(
            action=SetupAction(description=f"{verb_cap} '{package.name}' via {plugin_name}"),
            success=False,
            message=f"Plugin '{plugin_name}' is not available",
        )

    environment = environments[plugin_name]
    action = SetupAction(
        description=f"{verb_cap} '{package.name}' via {plugin_name}",
        kind=environment.plugin_kind(),
        ecosystem=environment.ecosystem(),
        installer=plugin_name,
        package=package,
        runtime_tag=runtime_tag,
    )

    ctx = ResolutionContext(runtime_context=runtime_context)

    if dry_run:
        resolved = await resolve_fn(action, environments, ctx)
        return resolved_to_result(resolved)

    event_queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
    result = await execute_fn(action, environments, event_queue, ctx)
    logger.info(
        '%s result: success=%s skipped=%s skip_reason=%s message=%s',
        verb,
        result.success,
        result.skipped,
        result.skip_reason,
        result.message,
    )
    return result


async def _discover_with_runtime(runtime_context: RuntimeContext | None) -> DiscoveredPlugins:
    """Discover plugins, resolving runtime context if not already provided."""
    plugins = await asyncio.to_thread(discover_all_plugins, use_cache=True)
    if runtime_context is None:
        plugins.runtime_context = await Builder.resolve_runtime_context(plugins.environments)
    return plugins


# Re-export for test patch compatibility
_discover_environments = discover_environments


async def _build_update_infos(
    env: Any,
    check_params: CheckUpdatesParameters,
    runtime_context: RuntimeContext,
) -> builtins.list[PackageUpdateInfo]:
    """Query an environment for package updates and return structured info."""
    installed = await env.packages(runtime_context=runtime_context)
    installed_map = {str(p.name): p for p in installed}

    updates = await env.check_updates(check_params)

    infos: builtins.list[PackageUpdateInfo] = []
    for update_pkg in updates:
        current = installed_map.get(str(update_pkg.name))
        current_version = Version(current.version) if current and current.version else None
        latest_version = Version(update_pkg.version) if update_pkg.version else None
        infos.append(
            PackageUpdateInfo(
                name=str(update_pkg.name),
                current_version=current_version,
                latest_version=latest_version,
                update_available=True,
            )
        )
    return infos


async def _check_env_updates(
    env: Environment,
    name: str,
    *,
    include_prereleases: bool,
    runtime_context: RuntimeContext,
    log_context: str = '',
) -> CheckResult:
    """Run an update check for a single environment, returning a ``CheckResult``.

    Builds :class:`CheckUpdatesParameters` for *env*, delegates to
    :func:`_build_update_infos`, and normalises any failure into a
    ``CheckResult`` carrying the error string.  Shared by
    ``check_updates`` and ``check_updates_by_runtime``.

    Args:
        env: The environment plugin to query.
        name: The plugin name (used in the result and log messages).
        include_prereleases: Whether to include pre-release versions.
        runtime_context: Resolved runtime paths for this check.
        log_context: Optional suffix appended to log messages (e.g.
            ``' on runtime 3.12'``) for extra diagnostic context.

    Returns:
        A ``CheckResult`` with either the discovered packages or an error.
    """
    try:
        check_params = CheckUpdatesParameters(
            packages=[],
            include_prereleases=include_prereleases,
            runtime_context=runtime_context,
        )
        package_infos = await _build_update_infos(env, check_params, runtime_context)
        return CheckResult(plugin=name, packages=package_infos)
    except (PluginError, UpdateError) as e:
        logger.error('Plugin error checking updates for %s%s: %s', name, log_context, e)
        return CheckResult(plugin=name, error=str(e))
    except Exception as e:
        logger.warning('Failed to check updates for %s%s: %s', name, log_context, e)
        return CheckResult(plugin=name, error=str(e))


# ---------------------------------------------------------------------------
# Public namespace
# ---------------------------------------------------------------------------


class PackageCommands:
    """Package operations on packages managed by environment plugins.

    All methods are static — the class acts as a namespace and does
    not require instantiation.

    This namespace covers *managed* packages (e.g. ``requests`` via pip,
    ``typescript`` via npm).  For managing *porringer extension* packages
    (e.g. ``porringer-plugin-apt``), see :class:`PluginCommands`.
    """

    # --- Listing ---

    @staticmethod
    async def list(
        plugin_name: str,
        project_path: Path | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> builtins.list[Package]:
        """List packages installed in a plugin's environment.

        Discovers the named plugin among ``environment`` plugins,
        initialises it, and returns the packages it reports as
        installed.

        When *plugins* is provided, the named environment is looked up
        directly — no entry-point scanning is performed.

        Args:
            plugin_name: The canonical plugin name to query.
            project_path: Path to the project directory.  ``None``
                queries the global / default environment.
            plugins: Pre-discovered plugins.
            runtime_context: Pre-resolved runtime context.

        Returns:
            The packages managed by the named plugin.

        Raises:
            PluginError: If the plugin is not found.
        """
        logger.debug('Listing packages for plugin: %s', plugin_name)

        if plugins is not None:
            environments = plugins.environments
            runtime_context = plugins.resolved_runtime(runtime_context)
        else:
            environments = _discover_environments()

        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        key = str(canonicalize_name(plugin_name))
        env = environments.get(key)
        if env is None:
            available = sorted(environments.keys())
            raise PluginError(f"Plugin '{plugin_name}' not found. Available: {', '.join(available)}")

        if not env.query_availability(runtime_context):
            logger.debug("Plugin '%s' is not available; returning empty package list", plugin_name)
            return []
        return await env.packages(project_path=project_path, runtime_context=runtime_context)

    @staticmethod
    async def list_by_runtime(
        plugin_name: str,
        *,
        project_path: Path | None = None,
        plugins: DiscoveredPlugins | None = None,
    ) -> builtins.list[RuntimePackageResult] | None:
        """List packages for every installed runtime of a consumer plugin.

        Discovers all runtime tags, resolves each to an interpreter
        executable, and queries the named plugin against every
        matching runtime.

        Args:
            plugin_name: The canonical plugin name (must be a
                :class:`RuntimeConsumer`).
            project_path: Path to the project directory.
            plugins: Pre-discovered plugins.

        Returns:
            One :class:`RuntimePackageResult` per queried runtime,
            or ``None`` if the plugin is not a :class:`RuntimeConsumer`.

        Raises:
            PluginError: If the plugin is not found.
        """
        logger.debug('Listing packages by runtime for plugin: %s', plugin_name)

        environments = plugins.environments if plugins is not None else _discover_environments()

        key = str(canonicalize_name(plugin_name))
        if key not in environments:
            available = sorted(environments.keys())
            raise PluginError(f"Plugin '{plugin_name}' not found. Available: {', '.join(available)}")

        env = environments[key]

        if not isinstance(env, RuntimeConsumer):
            return None

        consumed_kind = env.consumed_runtime_kind()

        all_runtimes = await Builder.resolve_all_runtime_executables(environments)

        matching = [rt for rt in all_runtimes if rt.kind == consumed_kind]
        if not matching:
            logger.debug("No runtimes of kind '%s' found for plugin '%s'", consumed_kind, plugin_name)
            return []

        async def _query(rt) -> RuntimePackageResult | None:
            ctx = RuntimeContext(executables={rt.kind: rt.executable})
            if not env.query_availability(ctx):
                logger.debug(
                    "Plugin '%s' not available for runtime %s (tag=%s)",
                    plugin_name,
                    rt.executable,
                    rt.tag,
                )
                return None
            pkgs = await env.packages(project_path=project_path, runtime_context=ctx)
            return RuntimePackageResult(
                provider=rt.provider,
                tag=rt.tag,
                executable=rt.executable,
                packages=pkgs,
            )

        gathered = await gather_bounded((lambda rt=rt: _query(rt) for rt in matching), limit=_DEFAULT_FANOUT)
        results = [r for r in gathered if r is not None]

        logger.debug(
            'list_by_runtime complete: %d runtime(s) queried for %s',
            len(results),
            plugin_name,
        )
        return results

    @staticmethod
    async def list_all_scopes(
        plugin_name: str,
        directories: builtins.list[Path],
        *,
        skip_global: bool = False,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> builtins.list[ScopedPackage]:
        """List packages across multiple scopes (global + per-directory).

        Fans out :meth:`list` via a ``TaskGroup`` across the global
        environment and each directory, returning a flat list of
        :class:`ScopedPackage` entries tagged with their origin.

        Args:
            plugin_name: The canonical plugin name to query.
            directories: Project directories to query.
            skip_global: When ``True``, skip the global (``project_path=None``) scope.
            plugins: Pre-discovered plugins.
            runtime_context: Pre-resolved runtime context.

        Returns:
            A flat list of scoped packages across all queried scopes.
        """
        logger.debug(
            'list_all_scopes: plugin=%s dirs=%d skip_global=%s',
            plugin_name,
            len(directories),
            skip_global,
        )

        # Resolve the runtime context once up front. Otherwise each fanned-out
        # scope query would independently call ``Builder.resolve_runtime_context``
        # (an expensive subprocess probe), turning one resolution into N+1.
        if runtime_context is None:
            if plugins is not None:
                runtime_context = plugins.resolved_runtime(None)
            if runtime_context is None:
                environments = plugins.environments if plugins is not None else _discover_environments()
                runtime_context = await Builder.resolve_runtime_context(environments)

        scoped: builtins.list[ScopedPackage] = []

        async def _query_scope(
            project_path: Path | None,
            label: str,
        ) -> builtins.list[ScopedPackage]:
            pkgs = await PackageCommands.list(
                plugin_name,
                project_path=project_path,
                plugins=plugins,
                runtime_context=runtime_context,
            )
            return [
                ScopedPackage(
                    package=pkg,
                    scope_label=label,
                    scope_path=project_path,
                    plugin_name=plugin_name,
                )
                for pkg in pkgs
            ]

        async with asyncio.TaskGroup() as tg:
            tasks: builtins.list[asyncio.Task[builtins.list[ScopedPackage]]] = []
            if not skip_global:
                tasks.append(tg.create_task(_query_scope(None, 'global')))
            for d in directories:
                tasks.append(tg.create_task(_query_scope(d, d.name)))

        for task in tasks:
            scoped.extend(task.result())

        logger.debug('list_all_scopes complete: %d scoped packages', len(scoped))
        return scoped

    @staticmethod
    async def list_with_manifest(
        plugin_name: str,
        manifest_path: Path,
        *,
        project_path: Path | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> tuple[builtins.list[Package], set[str]]:
        """List installed packages with manifest cross-referencing.

        Loads a manifest, extracts the package names declared for the
        given *plugin_name*, then lists installed packages.  Returns the
        installed packages and the set of canonical names that appear in
        the manifest so the caller can cross-reference.

        Args:
            plugin_name: The canonical plugin name to query.
            manifest_path: Path to the manifest file or directory.
            project_path: Path to the project directory.
            plugins: Pre-discovered plugins.
            runtime_context: Pre-resolved runtime context.

        Returns:
            A ``(packages, declared_names)`` tuple.  *declared_names*
            contains canonical package names present in the manifest
            for *plugin_name*.
        """
        logger.debug('list_with_manifest: plugin=%s manifest=%s', plugin_name, manifest_path)

        preview = await asyncio.to_thread(load_manifest, manifest_path)
        declared_names: set[str] = set()
        for action in preview.actions:
            if action.installer == plugin_name and action.package is not None:
                declared_names.add(str(canonicalize_name(action.package.name)))

        installed = await PackageCommands.list(
            plugin_name,
            project_path=project_path,
            plugins=plugins,
            runtime_context=runtime_context,
        )

        return installed, declared_names

    # --- Imperative operations ---

    @staticmethod
    async def install(
        plugin_name: str,
        package: PackageRef,
        *,
        runtime_tag: str | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
        dry_run: bool = False,
    ) -> SetupActionResult:
        """Install a package if it is not already present.

        Uses ``SyncStrategy.MINIMAL`` — when the package is already
        installed, the operation is skipped.

        Args:
            plugin_name: The installer plugin name (e.g. ``"pip"``).
            package: The package to install.
            runtime_tag: Optional runtime tag (e.g. ``"3.12"``).
            plugins: Pre-discovered plugins.
            runtime_context: Optional resolved runtime paths.
            dry_run: When ``True``, resolve only.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        return await _imperative_action(
            verb='install',
            plugin_name=plugin_name,
            package=package,
            runtime_tag=runtime_tag,
            plugins=plugins,
            runtime_context=runtime_context,
            dry_run=dry_run,
            resolve_fn=lambda action, envs, ctx: resolve_operation(action, envs, SyncStrategy.MINIMAL, ctx),
            execute_fn=lambda action, envs, queue, ctx: execute_package(
                action, envs, SyncStrategy.MINIMAL, queue, context=ctx
            ),
        )

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

        Uses ``SyncStrategy.LATEST`` — installs if absent, upgrades
        if already present.

        Args:
            plugin_name: The installer plugin name (e.g. ``"pipx"``).
            package: The package to upgrade.
            runtime_tag: Optional runtime tag (e.g. ``"3.12"``).
            plugins: Pre-discovered plugins.
            runtime_context: Optional resolved runtime paths.
            dry_run: When ``True``, resolve only.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        return await _imperative_action(
            verb='upgrade',
            plugin_name=plugin_name,
            package=package,
            runtime_tag=runtime_tag,
            plugins=plugins,
            runtime_context=runtime_context,
            dry_run=dry_run,
            resolve_fn=lambda action, envs, ctx: resolve_operation(action, envs, SyncStrategy.LATEST, ctx),
            execute_fn=lambda action, envs, queue, ctx: execute_package(
                action, envs, SyncStrategy.LATEST, queue, context=ctx
            ),
        )

    @staticmethod
    async def uninstall(
        plugin_name: str,
        package: PackageRef,
        *,
        runtime_tag: str | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
        dry_run: bool = False,
    ) -> SetupActionResult:
        """Uninstall a managed package.

        Resolves the named plugin, checks whether the package is
        installed, and runs the plugin's ``uninstall`` command.

        Args:
            plugin_name: The installer plugin name.
            package: The package to uninstall.
            runtime_tag: Optional runtime tag.
            plugins: Pre-discovered plugins.
            runtime_context: Optional resolved runtime paths.
            dry_run: When ``True``, resolve only.

        Returns:
            A ``SetupActionResult`` describing the outcome.
        """
        return await _imperative_action(
            verb='uninstall',
            plugin_name=plugin_name,
            package=package,
            runtime_tag=runtime_tag,
            plugins=plugins,
            runtime_context=runtime_context,
            dry_run=dry_run,
            resolve_fn=resolve_uninstall_operation,
            execute_fn=lambda action, envs, queue, ctx: execute_uninstall(action, envs, queue, context=ctx),
        )

    # --- Update checking ---

    @staticmethod
    async def check_updates(
        params: CheckParameters | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> builtins.list[CheckResult]:
        """Check for package updates across all (or selected) plugins.

        Args:
            params: Optional check parameters (plugin filter,
                pre-release flag).
            plugins: Pre-discovered plugins.

        Returns:
            One :class:`CheckResult` per queried plugin.
        """
        if params is None:
            params = CheckParameters()

        if plugins is None:
            plugins = await asyncio.to_thread(discover_all_plugins, use_cache=True)

        runtime_context = plugins.resolved_runtime()
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(plugins.environments)

        environments: dict[str, Environment] = dict(plugins.environments)

        eligible: builtins.list[tuple[str, Environment]] = []
        for name, env in environments.items():
            if params.plugins and name not in params.plugins:
                continue

            plugin_type = type(env)
            if not plugin_type.is_supported() or not env.query_availability(runtime_context):
                logger.debug('Skipping unavailable plugin %s for update check', name)
                continue

            eligible.append((name, env))

        # ``_check_env_updates`` isolates per-plugin failures into ``CheckResult``,
        # so these independent checks are safe to run concurrently. Bounded fan-out
        # keeps wall-clock near the slowest single check instead of their sum.
        resolved_context = runtime_context

        def _make_check(name: str, target_env: Environment) -> Callable[[], Awaitable[CheckResult]]:
            return lambda: _check_env_updates(
                target_env,
                name,
                include_prereleases=params.include_prereleases,
                runtime_context=resolved_context,
            )

        results = await gather_bounded(
            (_make_check(name, target_env) for name, target_env in eligible),
            limit=params.max_concurrency,
        )

        return results

    @staticmethod
    async def check_updates_by_runtime(
        params: CheckParameters | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> builtins.list[RuntimeCheckResult]:
        """Check for package updates per installed runtime.

        Args:
            params: Optional check parameters.
            plugins: Pre-discovered plugins.

        Returns:
            One :class:`RuntimeCheckResult` per queried runtime.
        """
        if params is None:
            params = CheckParameters()

        if plugins is not None:
            environments = plugins.environments
        else:
            environments = dict((await asyncio.to_thread(discover_all_plugins, use_cache=True)).environments)

        all_runtimes = await Builder.resolve_all_runtime_executables(environments)
        if not all_runtimes:
            return []

        consumers: dict[str, Any] = {}
        for name, env in environments.items():
            if params.plugins and name not in params.plugins:
                continue
            if not isinstance(env, RuntimeConsumer):
                continue
            if not env.is_supported():
                continue
            consumers[name] = env

        if not consumers:
            return []

        results: builtins.list[RuntimeCheckResult] = []

        async def _check_runtime(rt) -> RuntimeCheckResult | None:
            ctx = RuntimeContext(executables={rt.kind: rt.executable})

            eligible: builtins.list[tuple[str, Any]] = []
            for name, env in consumers.items():
                if env.consumed_runtime_kind() != rt.kind:
                    continue
                if not env.query_availability(ctx):
                    logger.debug(
                        "Plugin '%s' not available for runtime %s (tag=%s)",
                        name,
                        rt.executable,
                        rt.tag,
                    )
                    continue
                eligible.append((name, env))

            def _make_check(name: str, env: Any) -> Callable[[], Awaitable[CheckResult]]:
                return lambda: _check_env_updates(
                    env,
                    name,
                    include_prereleases=params.include_prereleases,
                    runtime_context=ctx,
                    log_context=f' on runtime {rt.tag}',
                )

            check_results = await gather_bounded(
                (_make_check(name, env) for name, env in eligible),
                limit=params.max_concurrency,
            )

            if not check_results:
                return None

            return RuntimeCheckResult(
                provider=rt.provider,
                tag=rt.tag,
                executable=rt.executable,
                results=check_results,
            )

        gathered = await gather_bounded(
            (lambda rt=rt: _check_runtime(rt) for rt in all_runtimes),
            limit=params.max_concurrency,
        )
        for entry in gathered:
            if entry is not None:
                results.append(entry)

        return results
