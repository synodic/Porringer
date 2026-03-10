"""Unified operation resolution for dry-run and real execution.

Determines the correct operation (install, upgrade, or skip) for a
given action based on the sync strategy and current system state.
Both the dry-run path and the real execution path delegate to
:func:`resolve_operation` so that strategy logic lives in one place.

Also hosts :func:`is_package_installed`, the shared presence-detection
helper used by resolution, dry-run, and execution paths.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.plugin_manager import PluginManager, find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import (
    Install,
    InstallReason,
    Operation,
    SetupAction,
    SetupActionResult,
    Skip,
    SkipReason,
    SyncStrategy,
    Uninstall,
    Upgrade,
)
from porringer.utility.exception import PluginError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResolvedOperation:
    """Result of resolving what operation an action requires.

    Produced by :func:`resolve_operation` and consumed by both the
    dry-run reporter and the real execution engine.
    """

    action: SetupAction
    operation: Operation
    message: str | None = None
    plugin_manager: PluginManager | None = None
    """The resolved ``PluginManager`` for plugin-target actions, or
    ``None`` for normal package actions.  Cached here so the caller
    does not need to look it up again."""


def resolved_to_result(resolved: ResolvedOperation) -> SetupActionResult:
    """Map a :class:`ResolvedOperation` to a :class:`SetupActionResult`.

    This is the single mapping used by both the dry-run and real
    execution paths so that the translation lives in one place.

    For :class:`Skip` operations the result carries version metadata
    and the skip reason.  For :class:`Upgrade` and :class:`Install`
    operations the result carries version metadata when available.
    """
    match resolved.operation:
        case Skip(reason=reason, installed_version=iv, available_version=av):
            logger.debug('resolved to skip: reason=%s message=%s', reason, resolved.message)
            return SetupActionResult(
                action=resolved.action,
                success=True,
                skipped=True,
                skip_reason=reason,
                message=resolved.message,
                installed_version=iv,
                available_version=av,
            )
        case Upgrade(installed_version=iv, available_version=av):
            return SetupActionResult(
                action=resolved.action,
                success=True,
                message=resolved.message,
                installed_version=iv,
                available_version=av,
            )
        case Install(installed_version=iv):
            return SetupActionResult(
                action=resolved.action,
                success=True,
                message=resolved.message,
                installed_version=iv,
            )
        case _:
            return SetupActionResult(
                action=resolved.action,
                success=True,
                message=resolved.message,
            )


class PackageCache:
    """Per-phase cache for ``packages()`` / ``installed_plugins()`` results.

    Ensures each environment plugin's ``packages()`` method and each
    ``PluginManager``'s ``installed_plugins()`` method is called at
    most once per unique key, eliminating redundant subprocess or
    filesystem queries when many actions share the same installer.

    Thread-safe via per-key `asyncio.Lock` instances so concurrent
    ``TaskGroup`` tasks that hit the same installer serialise on
    the first query and share its result.
    """

    def __init__(self) -> None:
        """Initialise empty caches and lock registry."""
        self._packages: dict[str, list[Package]] = {}
        self._plugins: dict[str, list[Package]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        """Return (creating if needed) the lock for *key*."""
        return self._locks.setdefault(key, asyncio.Lock())

    async def get_packages(
        self,
        installer: str,
        environment: Environment,
        project_path: Path | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> list[Package]:
        """Return cached ``packages()`` result, querying on first access.

        The cache key is ``(installer, project_path)`` so that
        project-scoped queries are cached separately from global ones.

        Args:
            installer: The installer/plugin name.
            environment: The environment plugin instance.
            project_path: Optional project directory scope.
            runtime_context: Resolved runtime paths for this execution run.

        Returns:
            The list of installed packages.
        """
        key = f'pkg:{installer}:{project_path}'
        async with self._lock_for(key):
            if key not in self._packages:
                self._packages[key] = await environment.packages(
                    project_path=project_path, runtime_context=runtime_context
                )
            return self._packages[key]

    async def get_plugins(
        self,
        tool_name: str,
        manager: PluginManager,
    ) -> list[Package]:
        """Return cached ``installed_plugins()`` result.

        Args:
            tool_name: The host tool name (cache key).
            manager: The plugin manager instance.

        Returns:
            The list of installed plugin packages.
        """
        key = f'plg:{tool_name}'
        async with self._lock_for(key):
            if key not in self._plugins:
                self._plugins[key] = await manager.installed_plugins()
            return self._plugins[key]

    def invalidate_packages(self, installer: str, project_path: Path | None = None) -> None:
        """Remove cached packages for an installer so next access re-queries.

        Call this after a successful install/upgrade so subsequent
        presence checks see fresh state.

        Args:
            installer: The installer/plugin name.
            project_path: Optional project directory scope.
        """
        key = f'pkg:{installer}:{project_path}'
        self._packages.pop(key, None)

    def invalidate_plugins(self, tool_name: str) -> None:
        """Remove cached plugins for a tool so next access re-queries.

        Args:
            tool_name: The host tool name.
        """
        key = f'plg:{tool_name}'
        self._plugins.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear all cached data.

        Locks are intentionally retained — clearing them while a
        concurrent coroutine holds one would be unsafe.
        """
        self._packages.clear()
        self._plugins.clear()


@dataclass(slots=True)
class ResolutionContext:
    """Optional context for :func:`resolve_operation`.

    Groups the keyword-only parameters so the public API stays concise.
    """

    project_path: Path | None = None
    """Project directory for scoped package queries."""
    project_environments: dict[str, ProjectEnvironment] | None = None
    """Dict of project-environment plugins, used to look up
    ``PluginManager`` instances for plugin-target actions."""
    http_client: httpx.AsyncClient | None = None
    """Shared ``httpx.AsyncClient`` for connection pooling across
    concurrent update checks.  ``None`` means each check creates
    its own short-lived client."""
    package_cache: PackageCache | None = None
    """Optional shared cache for ``packages()`` results.  When set,
    multiple actions using the same installer share a single
    ``packages()`` call instead of querying independently."""
    runtime_context: RuntimeContext | None = None
    """Resolved runtime paths.  Threaded through to ``packages()``
    so Python-ecosystem plugins can query packages from the correct
    interpreter."""


async def resolve_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    context: ResolutionContext | None = None,
) -> ResolvedOperation:
    """Determine the correct operation for an action.

    Queries the system for installed packages/plugins, applies the
    strategy, and optionally checks for upstream updates.  The
    returned :class:`ResolvedOperation` tells the caller whether to
    install, upgrade, or skip — and carries version metadata for
    display purposes.

    This function is intentionally **side-effect-free**: it reads
    system state but does not modify it.

    Args:
        action: The action to resolve.
        environments: Dict of instantiated environment plugins.
        strategy: The sync strategy.
        context: Optional resolution context with project path,
            project environments, and update-detection flag.

    Returns:
        A fully resolved operation descriptor.
    """
    ctx = context or ResolutionContext()

    if action.installer is None or action.package is None:
        return ResolvedOperation(
            action=action,
            operation=Skip(),
            message='Installer or package not specified',
        )

    # --- Plugin-management actions -----------------------------------------
    if action.plugin_target is not None:
        return await _resolve_plugin_operation(action, environments, strategy, ctx)

    # --- Normal package actions --------------------------------------------
    return await _resolve_package_operation(action, environments, strategy, ctx)


async def _resolve_plugin_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    ctx: ResolutionContext,
) -> ResolvedOperation:
    """Resolve the operation for a plugin-management action."""
    assert action.plugin_target is not None
    assert action.package is not None

    manager = find_plugin_manager(action.plugin_target.name, ctx.project_environments)
    if manager is None:
        # No PluginManager found — cannot determine presence, assume install
        return ResolvedOperation(
            action=action,
            operation=Install(),
            plugin_manager=None,
            message='PluginManager not available for query',
        )

    # Query installed plugins — use cache when available
    presence = _PresenceResult(
        env_for_updates=environments.get(action.installer) if action.installer else None,
    )
    try:
        if ctx.package_cache is not None:
            installed = await ctx.package_cache.get_plugins(action.plugin_target.name, manager)
        else:
            installed = await manager.installed_plugins()
        presence.is_installed, presence.detail, presence.matched = is_package_installed(action.package, installed)
    except Exception as e:
        logger.debug('Could not check installed plugins for %s: %s', action.plugin_target.name, e)

    return await _apply_strategy(
        action=action,
        strategy=strategy,
        presence=presence,
        plugin_manager=manager,
        http_client=ctx.http_client,
        runtime_context=ctx.runtime_context,
    )


async def _resolve_package_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    ctx: ResolutionContext,
) -> ResolvedOperation:
    """Resolve the operation for a normal package action."""
    assert action.installer is not None
    assert action.package is not None

    if action.installer not in environments:
        return ResolvedOperation(
            action=action,
            operation=Install(),
            message=f"Installer '{action.installer}' is not available",
        )

    environment = environments[action.installer]
    validator = type(environment).package_name_validator()

    presence = _PresenceResult(env_for_updates=environment)
    try:
        if ctx.package_cache is not None:
            installed_packages = await ctx.package_cache.get_packages(
                action.installer, environment, ctx.project_path, ctx.runtime_context
            )
        else:
            installed_packages = await environment.packages(
                project_path=ctx.project_path, runtime_context=ctx.runtime_context
            )
        presence.is_installed, presence.detail, presence.matched = is_package_installed(
            action.package, installed_packages, validator, action.kind
        )
    except PluginError as e:
        logger.debug('Plugin error checking packages for %s: %s', action.installer, e)
    except Exception as e:
        logger.debug('Could not check installed packages for %s: %s', action.installer, e)

    return await _apply_strategy(
        action=action,
        strategy=strategy,
        presence=presence,
        http_client=ctx.http_client,
        runtime_context=ctx.runtime_context,
    )


@dataclass(slots=True)
class _PresenceResult:
    """Result of querying whether a package/plugin is installed."""

    is_installed: bool = False
    detail: str | None = None
    matched: Package | None = None
    env_for_updates: Environment | None = None
    """Environment plugin to use for upstream update checks."""


async def _resolve_latest_installed(
    *,
    action: SetupAction,
    presence: _PresenceResult,
    installed_ver: str | None,
    has_extras: bool,
    plugin_manager: PluginManager | None,
    http_client: httpx.AsyncClient | None,
    runtime_context: RuntimeContext | None,
) -> ResolvedOperation:
    """Resolve a LATEST/EXACT operation for an already-installed package.

    Queries for a newer version when possible.  Falls back to an
    unconditional upgrade when no update-check environment is available.
    """
    if presence.env_for_updates is not None:
        try:
            newer = await check_for_newer_version(
                presence.env_for_updates,
                action.package,
                installed_ver,
                include_prereleases=action.include_prereleases,
                http_client=http_client,
                runtime_context=runtime_context,
            )
        except UpdateCheckError:
            pass  # Fall through to unconditional upgrade
        else:
            if newer is not None:
                pkg_name = action.package.name if action.package else ''
                return ResolvedOperation(
                    action=action,
                    operation=Upgrade(
                        installed_version=installed_ver,
                        available_version=newer,
                    ),
                    message=f'{pkg_name} {installed_ver} → {newer}',
                    plugin_manager=plugin_manager,
                )
            # Version is latest but extras may have changed.
            if has_extras:
                return ResolvedOperation(
                    action=action,
                    operation=Install(
                        reason=InstallReason.ENSURE_EXTRAS,
                        installed_version=installed_ver,
                    ),
                    message='ensuring extras',
                    plugin_manager=plugin_manager,
                )
            return ResolvedOperation(
                action=action,
                operation=Skip(
                    reason=SkipReason.ALREADY_LATEST,
                    installed_version=installed_ver,
                ),
                message=presence.detail,
                plugin_manager=plugin_manager,
            )

    # No environment for update checks, or check failed — upgrade
    # unconditionally.
    return ResolvedOperation(
        action=action,
        operation=Upgrade(),
        message=presence.detail,
        plugin_manager=plugin_manager,
    )


async def _apply_strategy(
    *,
    action: SetupAction,
    strategy: SyncStrategy,
    presence: _PresenceResult,
    plugin_manager: PluginManager | None = None,
    http_client: httpx.AsyncClient | None = None,
    runtime_context: RuntimeContext | None = None,
) -> ResolvedOperation:
    """Apply the sync strategy to determine the operation.

    This is the single source of truth for the install/upgrade/skip
    decision.  Both normal packages and plugin-management actions
    share this logic.

    When an already-installed package is skipped under MINIMAL
    strategy, an upstream version check is always performed so that
    callers receive populated version metadata.
    """
    installed_ver = presence.matched.version if presence.matched else None
    has_extras = action.package is not None and bool(action.package.extras)

    if strategy == SyncStrategy.MINIMAL:
        if presence.is_installed:
            # Extras cannot be introspected from installed state — always
            # re-run the install command so the underlying tool ensures
            # the requested extras/features are satisfied.
            if has_extras:
                return ResolvedOperation(
                    action=action,
                    operation=Install(
                        reason=InstallReason.ENSURE_EXTRAS,
                        installed_version=installed_ver,
                    ),
                    message='ensuring extras',
                    plugin_manager=plugin_manager,
                )

            # Always check for updates so version metadata is populated
            skip_reason = SkipReason.ALREADY_INSTALLED
            available_ver: str | None = None
            msg: str | None = presence.detail

            if presence.env_for_updates is not None:
                try:
                    newer = await check_for_newer_version(
                        presence.env_for_updates,
                        action.package,
                        installed_ver,
                        include_prereleases=action.include_prereleases,
                        http_client=http_client,
                        runtime_context=runtime_context,
                    )
                except UpdateCheckError:
                    newer = None

                if newer is not None:
                    skip_reason = SkipReason.UPDATE_AVAILABLE
                    available_ver = newer
                    pkg_name = action.package.name if action.package else ''
                    msg = f'{pkg_name} {installed_ver} → {available_ver}'

            return ResolvedOperation(
                action=action,
                operation=Skip(
                    reason=skip_reason,
                    installed_version=installed_ver,
                    available_version=available_ver,
                ),
                message=msg,
                plugin_manager=plugin_manager,
            )
        # Not installed → install
        return ResolvedOperation(
            action=action,
            operation=Install(),
            plugin_manager=plugin_manager,
        )

    # LATEST or EXACT strategy
    if presence.is_installed:
        return await _resolve_latest_installed(
            action=action,
            presence=presence,
            installed_ver=installed_ver,
            has_extras=has_extras,
            plugin_manager=plugin_manager,
            http_client=http_client,
            runtime_context=runtime_context,
        )

    # Not installed under LATEST/EXACT → fall back to install
    return ResolvedOperation(
        action=action,
        operation=Install(),
        message='not installed, will install instead',
        plugin_manager=plugin_manager,
    )


async def resolve_uninstall_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    context: ResolutionContext | None = None,
) -> ResolvedOperation:
    """Determine whether a package can be uninstalled.

    Checks the system for the package's presence and returns
    :class:`Uninstall` when found or :class:`Skip` with
    ``NOT_INSTALLED`` when the package is absent.

    Unlike :func:`resolve_operation`, this function is not
    strategy-driven — uninstall is an imperative operation.

    Args:
        action: The action describing the package to uninstall.
        environments: Dict of instantiated environment plugins.
        context: Optional resolution context with project path and
            package cache.

    Returns:
        A resolved operation descriptor.
    """
    ctx = context or ResolutionContext()

    if action.installer is None or action.package is None:
        return ResolvedOperation(
            action=action,
            operation=Skip(),
            message='Installer or package not specified',
        )

    # --- Plugin-management actions -----------------------------------------
    if action.plugin_target is not None:
        return await _resolve_plugin_uninstall(action, ctx)

    # --- Normal package actions --------------------------------------------
    if action.installer not in environments:
        return ResolvedOperation(
            action=action,
            operation=Skip(),
            message=f"Installer '{action.installer}' is not available",
        )

    environment = environments[action.installer]
    validator = type(environment).package_name_validator()

    try:
        if ctx.package_cache is not None:
            installed_packages = await ctx.package_cache.get_packages(
                action.installer, environment, ctx.project_path, ctx.runtime_context
            )
        else:
            installed_packages = await environment.packages(
                project_path=ctx.project_path, runtime_context=ctx.runtime_context
            )
        logger.debug('packages query for %s returned %d entries', action.installer, len(installed_packages))
        is_installed, detail, matched = is_package_installed(action.package, installed_packages, validator, action.kind)
        logger.debug(
            "is_package_installed('%s'): found=%s matched=%s",
            action.package.name,
            is_installed,
            matched.name if matched else None,
        )
    except Exception as e:
        logger.debug('Could not check installed packages for %s: %s', action.installer, e)
        is_installed, detail, matched = False, None, None

    if not is_installed:
        return ResolvedOperation(
            action=action,
            operation=Skip(reason=SkipReason.NOT_INSTALLED),
            message=f"'{action.package.name}' is not installed",
        )

    return ResolvedOperation(
        action=action,
        operation=Uninstall(installed_version=matched.version if matched else None),
        message=detail,
    )


async def _resolve_plugin_uninstall(
    action: SetupAction,
    ctx: ResolutionContext,
) -> ResolvedOperation:
    """Resolve an uninstall operation for a plugin-management action.

    Args:
        action: The action describing the plugin to uninstall.
        ctx: Resolution context with project environments and cache.

    Returns:
        A resolved operation descriptor.
    """
    assert action.plugin_target is not None
    assert action.package is not None

    manager = find_plugin_manager(action.plugin_target.name, ctx.project_environments)
    if manager is None:
        return ResolvedOperation(
            action=action,
            operation=Skip(),
            message=f"No PluginManager found for '{action.plugin_target.name}'",
        )

    try:
        if ctx.package_cache is not None:
            installed = await ctx.package_cache.get_plugins(action.plugin_target.name, manager)
        else:
            installed = await manager.installed_plugins()
        is_installed, detail, matched = is_package_installed(action.package, installed)
    except Exception as e:
        logger.debug('Could not check installed plugins for %s: %s', action.plugin_target.name, e)
        is_installed, detail, matched = False, None, None

    if not is_installed:
        return ResolvedOperation(
            action=action,
            operation=Skip(reason=SkipReason.NOT_INSTALLED),
            message=f"Plugin '{action.package.name}' is not installed in '{action.plugin_target.name}'",
            plugin_manager=manager,
        )
    return ResolvedOperation(
        action=action,
        operation=Uninstall(installed_version=matched.version if matched else None),
        message=detail,
        plugin_manager=manager,
    )


class UpdateCheckError(Exception):
    """The update check could not be performed.

    The caller should fall back to a conservative action
    (e.g. attempt the upgrade anyway).
    """


async def check_for_newer_version(
    env: Environment,
    package: PackageRef | None,
    installed_version: str | None,
    *,
    include_prereleases: bool = False,
    http_client: httpx.AsyncClient | None = None,
    runtime_context: RuntimeContext | None = None,
) -> str | None:
    """Query the plugin for a newer upstream version.

    Uses the plugin's ``check_updates()`` coroutine.  When
    *http_client* is provided it is forwarded via
    ``CheckUpdatesParameters`` so that concurrent checks share a
    single connection pool.

    Returns the newer version string when one is available, or ``None``
    when the installed version is confirmed up-to-date.

    Raises:
        UpdateCheckError: When the check cannot complete (network
            error, missing package, etc.).
    """
    if package is None:
        raise UpdateCheckError('no package reference')

    try:
        params = CheckUpdatesParameters(
            packages=[package],
            include_prereleases=include_prereleases,
            http_client=http_client,
            runtime_context=runtime_context,
        )
        updates = await env.check_updates(params)
    except Exception as e:
        logger.debug('check_updates failed for %s via %s: %s', package, env.tool_name(), e)
        raise UpdateCheckError(str(e)) from e

    if not updates or updates[0].version is None:
        # Plugin responded but reported no updates — confirmed up-to-date.
        return None

    latest_ver = updates[0].version

    # Defense-in-depth: reject pre-release versions when the caller
    # did not opt in, even if the plugin failed to filter them.
    if not include_prereleases:
        try:
            if Version(latest_ver).is_prerelease:
                return None
        except InvalidVersion:
            pass  # Non-PEP-440 version string — let the comparison below decide

    # Compare versions when possible to avoid false positives
    if installed_version is not None:
        try:
            if Version(latest_ver) <= Version(installed_version):
                return None
        except InvalidVersion:
            if latest_ver == installed_version:
                return None

    return latest_ver


# ---------------------------------------------------------------------------
# Presence detection
# ---------------------------------------------------------------------------


def is_package_installed(
    package: PackageRef,
    installed_packages: list[Package],
    name_validator: str | None = None,
    kind: PluginKind | None = None,
) -> tuple[bool, str | None, Package | None]:
    """Checks if a package is already installed with a compatible version.

    When *name_validator* is ``'pep440'``, uses PEP 440 canonicalization
    and specifier matching.  Otherwise, uses case-insensitive name
    comparison and simple string version equality.

    For ``RUNTIME`` actions, name comparison uses prefix matching so
    that a request for ``3.14`` matches an installed ``3.14-64``
    (architecture-qualified tag).

    Args:
        package: The package reference
        installed_packages: List of installed packages from the environment
        name_validator: The validator tag declared by the resolved plugin
        kind: The plugin kind (enables prefix matching for RUNTIME)

    Returns:
        Tuple of (is_installed, detail_message, matched_package).
        ``matched_package`` is the ``Package`` object that matched,
        or ``None`` when the package is not installed.
    """
    is_pep440 = name_validator == 'pep440'
    is_runtime = kind == PluginKind.RUNTIME

    for installed in installed_packages:
        if is_runtime:
            # Runtime tags use prefix matching: "3.14" matches "3.14-64"
            if not installed.name.lower().startswith(package.name.lower()):
                continue
        elif is_pep440:
            if canonicalize_name(installed.name) != canonicalize_name(package.name):
                continue
        elif installed.name.lower() != package.name.lower():
            continue

        # Name matched
        if not package.constraint:
            return True, f'{installed.name}=={installed.version} already installed', installed

        if installed.version is not None:
            if is_pep440:
                try:
                    req = Requirement(str(package))
                    if Version(installed.version) in req.specifier:
                        return True, f'{installed.name}=={installed.version} satisfies {package}', installed
                except InvalidVersion, InvalidRequirement:
                    pass
            else:
                # Non-PEP-440: installed means installed; constraint
                # satisfaction is left to the underlying tool.
                return True, f'{installed.name}=={installed.version} already installed', installed

    return False, None, None
