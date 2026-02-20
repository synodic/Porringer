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
from enum import Enum, auto
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.plugin_manager import PluginManager, find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SkipReason,
    SyncStrategy,
)
from porringer.utility.exception import PluginError

logger = logging.getLogger(__name__)


class OperationKind(Enum):
    """The resolved operation to perform on a package."""

    INSTALL = auto()
    UPGRADE = auto()
    SKIP = auto()


@dataclass
class ResolvedOperation:
    """Result of resolving what operation an action requires.

    Produced by :func:`resolve_operation` and consumed by both the
    dry-run reporter and the real execution engine.
    """

    action: SetupAction
    operation: OperationKind
    skip_reason: SkipReason | None = None
    message: str | None = None
    installed_version: str | None = None
    available_version: str | None = None
    plugin_manager: PluginManager | None = None
    """The resolved ``PluginManager`` for plugin-target actions, or
    ``None`` for normal package actions.  Cached here so the caller
    does not need to look it up again."""


def resolved_to_result(resolved: ResolvedOperation) -> SetupActionResult:
    """Map a :class:`ResolvedOperation` to a :class:`SetupActionResult`.

    This is the single mapping used by both the dry-run and real
    execution paths so that the translation lives in one place.

    For ``SKIP`` operations the result carries version metadata and
    the skip reason.  For ``INSTALL`` and ``UPGRADE`` operations
    the result reports success with an optional message.
    """
    if resolved.operation == OperationKind.SKIP:
        return SetupActionResult(
            action=resolved.action,
            success=True,
            skipped=True,
            skip_reason=resolved.skip_reason,
            message=resolved.message,
            installed_version=resolved.installed_version,
            available_version=resolved.available_version,
        )

    return SetupActionResult(
        action=resolved.action,
        success=True,
        message=resolved.message,
    )


@dataclass
class ResolutionContext:
    """Optional context for :func:`resolve_operation`.

    Groups the keyword-only parameters so the public API stays concise.
    """

    project_path: Path | None = None
    """Project directory for scoped package queries."""
    project_environments: dict[str, ProjectEnvironment] | None = None
    """Dict of project-environment plugins, used to look up
    ``PluginManager`` instances for plugin-target actions."""
    detect_updates: bool = False
    """When ``True`` and the package is already installed under
    ``MINIMAL`` strategy, check for newer upstream versions."""


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
            operation=OperationKind.SKIP,
            message='Installer or package not specified',
        )

    # --- Plugin-management actions -----------------------------------------
    if action.plugin_target is not None:
        return await _resolve_plugin_operation(
            action,
            environments,
            strategy,
            project_environments=ctx.project_environments,
            detect_updates=ctx.detect_updates,
        )

    # --- Normal package actions --------------------------------------------
    return await _resolve_package_operation(
        action,
        environments,
        strategy,
        project_path=ctx.project_path,
        detect_updates=ctx.detect_updates,
    )


async def _resolve_plugin_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    *,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    detect_updates: bool = False,
) -> ResolvedOperation:
    """Resolve the operation for a plugin-management action."""
    assert action.plugin_target is not None
    assert action.package is not None

    manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if manager is None:
        # No PluginManager found — cannot determine presence, assume install
        return ResolvedOperation(
            action=action,
            operation=OperationKind.INSTALL,
            plugin_manager=None,
            message='PluginManager not available for query',
        )

    # Query installed plugins
    presence = _PresenceResult(
        env_for_updates=environments.get(action.installer) if action.installer else None,
    )
    try:
        installed = manager.installed_plugins()
        presence.is_installed, presence.detail, presence.matched = is_package_installed(action.package, installed)
    except Exception as e:
        logger.debug('Could not check installed plugins for %s: %s', action.plugin_target.name, e)

    return _apply_strategy(
        action=action,
        strategy=strategy,
        presence=presence,
        detect_updates=detect_updates,
        plugin_manager=manager,
    )


async def _resolve_package_operation(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    *,
    project_path: Path | None = None,
    detect_updates: bool = False,
) -> ResolvedOperation:
    """Resolve the operation for a normal package action."""
    assert action.installer is not None
    assert action.package is not None

    if action.installer not in environments:
        return ResolvedOperation(
            action=action,
            operation=OperationKind.INSTALL,
            message=f"Installer '{action.installer}' is not available",
        )

    environment = environments[action.installer]
    validator = type(environment).package_name_validator()

    presence = _PresenceResult(env_for_updates=environment)
    try:
        loop = asyncio.get_running_loop()
        installed_packages = await loop.run_in_executor(None, lambda: environment.packages(project_path=project_path))
        presence.is_installed, presence.detail, presence.matched = is_package_installed(
            action.package, installed_packages, validator, action.kind
        )
    except PluginError as e:
        logger.debug('Plugin error checking packages for %s: %s', action.installer, e)
    except Exception as e:
        logger.debug('Could not check installed packages for %s: %s', action.installer, e)

    return _apply_strategy(
        action=action,
        strategy=strategy,
        presence=presence,
        detect_updates=detect_updates,
    )


@dataclass
class _PresenceResult:
    """Result of querying whether a package/plugin is installed."""

    is_installed: bool = False
    detail: str | None = None
    matched: Package | None = None
    env_for_updates: Environment | None = None
    """Environment plugin to use for upstream update checks."""


def _apply_strategy(
    *,
    action: SetupAction,
    strategy: SyncStrategy,
    presence: _PresenceResult,
    detect_updates: bool,
    plugin_manager: PluginManager | None = None,
) -> ResolvedOperation:
    """Apply the sync strategy to determine the operation.

    This is the single source of truth for the install/upgrade/skip
    decision.  Both normal packages and plugin-management actions
    share this logic.
    """
    installed_ver = presence.matched.version if presence.matched else None

    if strategy == SyncStrategy.MINIMAL:
        if presence.is_installed:
            # Check for updates if requested (dry-run feature)
            skip_reason = SkipReason.ALREADY_INSTALLED
            available_ver: str | None = None
            msg: str | None = presence.detail

            if detect_updates and presence.env_for_updates is not None:
                check = _check_for_newer_version(
                    presence.env_for_updates,
                    action.package,
                    installed_ver,
                    include_prereleases=action.include_prereleases,
                )
                if check.newer_version is not None:
                    skip_reason = SkipReason.UPDATE_AVAILABLE
                    available_ver = check.newer_version
                    pkg_name = action.package.name if action.package else ''
                    msg = f'{pkg_name} {installed_ver} → {available_ver}'

            return ResolvedOperation(
                action=action,
                operation=OperationKind.SKIP,
                skip_reason=skip_reason,
                message=msg,
                installed_version=installed_ver,
                available_version=available_ver,
                plugin_manager=plugin_manager,
            )
        else:
            # Not installed → install
            return ResolvedOperation(
                action=action,
                operation=OperationKind.INSTALL,
                plugin_manager=plugin_manager,
            )

    # LATEST or EXACT strategy
    if presence.is_installed:
        # Check whether this package actually has a newer version
        # available before attempting an upgrade.  When the plugin
        # confirms the package is already at its latest version we
        # can skip the no-op upgrade entirely.
        if presence.env_for_updates is not None:
            check = _check_for_newer_version(
                presence.env_for_updates,
                action.package,
                installed_ver,
                include_prereleases=action.include_prereleases,
            )
            if not check.error:
                if check.newer_version is not None:
                    pkg_name = action.package.name if action.package else ''
                    return ResolvedOperation(
                        action=action,
                        operation=OperationKind.UPGRADE,
                        message=f'{pkg_name} {installed_ver} → {check.newer_version}',
                        installed_version=installed_ver,
                        available_version=check.newer_version,
                        plugin_manager=plugin_manager,
                    )
                # Confirmed up-to-date — skip.
                return ResolvedOperation(
                    action=action,
                    operation=OperationKind.SKIP,
                    skip_reason=SkipReason.ALREADY_LATEST,
                    message=presence.detail,
                    installed_version=installed_ver,
                    plugin_manager=plugin_manager,
                )
            # Could not determine upstream state — fall through
            # to attempt the upgrade conservatively.

        # No environment for update checks, or check failed — upgrade
        # unconditionally.
        return ResolvedOperation(
            action=action,
            operation=OperationKind.UPGRADE,
            message=presence.detail,
            installed_version=installed_ver,
            plugin_manager=plugin_manager,
        )

    # Not installed under LATEST/EXACT → fall back to install
    return ResolvedOperation(
        action=action,
        operation=OperationKind.INSTALL,
        message='not installed, will install instead',
        plugin_manager=plugin_manager,
    )


@dataclass(slots=True)
class _UpdateCheckResult:
    """Result of a newer-version check.

    Distinguishes three outcomes:

    * **newer version found** — ``newer_version`` is a string.
    * **confirmed up-to-date** — ``newer_version`` is ``None`` and
      ``error`` is ``False``.
    * **check failed** — ``newer_version`` is ``None`` and ``error``
      is ``True``.  The caller should fall back to a conservative
      action (e.g. attempt the upgrade anyway).
    """

    newer_version: str | None = None
    error: bool = False


def _check_for_newer_version(
    env: Environment,
    package: PackageRef | None,
    installed_version: str | None,
    *,
    include_prereleases: bool = False,
) -> _UpdateCheckResult:
    """Query the plugin for a newer upstream version.

    Returns an :class:`_UpdateCheckResult` that distinguishes *newer
    version found*, *confirmed up-to-date*, and *check failed*.
    """
    if package is None:
        return _UpdateCheckResult(error=True)

    try:
        updates = env.check_updates(
            CheckUpdatesParameters(
                packages=[package],
                include_prereleases=include_prereleases,
            )
        )
    except Exception as e:
        logger.debug('check_updates failed for %s via %s: %s', package, env.tool_name(), e)
        return _UpdateCheckResult(error=True)

    if not updates or updates[0].version is None:
        # Plugin responded but reported no updates — confirmed up-to-date.
        return _UpdateCheckResult()

    latest_ver = updates[0].version

    # Compare versions when possible to avoid false positives
    if installed_version is not None:
        try:
            if Version(latest_ver) <= Version(installed_version):
                return _UpdateCheckResult()
        except InvalidVersion:
            if latest_ver == installed_version:
                return _UpdateCheckResult()

    return _UpdateCheckResult(newer_version=latest_ver)


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
