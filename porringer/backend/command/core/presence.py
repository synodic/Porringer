"""Presence detection and dry-run simulation.

Checks whether packages are already installed so the sync engine can
skip redundant operations.
"""

import logging
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.plugin_manager import find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)

logger = logging.getLogger(__name__)


def dry_run_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_path: Path | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulates executing an action in dry-run mode.

    For package/tool/runtime actions, real system state is checked so
    that the result accurately reflects whether the action would be
    skipped.

    Post-sync commands (`kind is None`) always report success since
    they would unconditionally run during a real execution.

    Args:
        action: The action to simulate.
        environments: Dict of instantiated environment plugins.
        project_path: Optional project directory for scoped package queries.
        project_environments: Optional dict of project-environment
            plugins, used to look up ``PluginManager`` instances for
            plugin-target presence checks.
        parameters: Full setup parameters.  When provided, ``strategy``
            and ``detect_updates`` are read from it.  When ``None``,
            ``SyncStrategy.MINIMAL`` is used.

    Returns:
        The simulated result.
    """
    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            return _dry_run_package_action(
                action,
                environments,
                project_path=project_path,
                project_environments=project_environments,
                parameters=parameters,
            )
        case PluginKind.PROJECT | PluginKind.SCM | None:
            return SetupActionResult(action=action, success=True)
        case _:
            return SetupActionResult(action=action, success=False, message=f'Unknown action kind: {action.kind}')


def _dry_run_package_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_path: Path | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate a package action in dry-run mode.

    When ``parameters.detect_updates`` is ``True`` and the package
    is already installed under ``MINIMAL`` strategy, the plugin's
    ``check_updates`` method is called to discover whether a newer
    upstream version exists.  If one does, the result carries
    ``SkipReason.UPDATE_AVAILABLE`` together with structured version
    fields for GUI consumption.
    """
    strategy = parameters.strategy if parameters else SyncStrategy.MINIMAL
    if action.installer is None or action.package is None or action.installer not in environments:
        return SetupActionResult(action=action, success=True)

    # --- Plugin-target actions: query the PluginManager, not the installer ---
    if action.plugin_target is not None:
        return _dry_run_plugin_action(
            action,
            environments,
            project_environments=project_environments,
            parameters=parameters,
        )

    # Determine name validator from the plugin
    env = environments[action.installer]
    validator = type(env).package_name_validator()

    try:
        installed_packages = environments[action.installer].packages(project_path=project_path)
        is_installed, installed_detail, matched_package = is_package_installed(
            action.package, installed_packages, validator, action.kind
        )
    except Exception as e:
        logger.debug(f'Dry-run: could not check installed packages for {action.installer}: {e}')
        return SetupActionResult(action=action, success=True)

    if strategy == SyncStrategy.MINIMAL:
        if is_installed:
            installed_ver = matched_package.version if matched_package else None

            # --- Update detection (opt-in) --------------------------------
            skip_reason = SkipReason.ALREADY_INSTALLED
            available_ver: str | None = None
            msg: str | None = installed_detail

            if parameters is not None and parameters.detect_updates:
                newer = _check_for_newer_version(
                    env,
                    action.package,
                    installed_ver,
                    include_prereleases=action.include_prereleases,
                )
                if newer is not None:
                    skip_reason = SkipReason.UPDATE_AVAILABLE
                    available_ver = newer
                    msg = f'{action.package.name} {installed_ver} → {available_ver}'
                    logger.info(f"Dry-run: update available for '{action.package}': {msg}")
                else:
                    logger.info(f"Dry-run: skipping '{action.package}': {installed_detail}")
            else:
                logger.info(f"Dry-run: skipping '{action.package}': {installed_detail}")

            return SetupActionResult(
                action=action,
                success=True,
                skipped=True,
                skip_reason=skip_reason,
                message=msg,
                installed_version=installed_ver,
                available_version=available_ver,
            )
    elif not is_installed:
        return SetupActionResult(
            action=action,
            success=True,
            message='not installed, will install instead',
        )

    return SetupActionResult(action=action, success=True)


def _dry_run_plugin_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate a plugin-management action in dry-run mode.

    Locates the ``PluginManager`` for the target tool and queries
    its installed plugins to determine whether the action would be
    skipped.  When ``parameters.detect_updates`` is ``True``, the
    installer environment's ``check_updates`` is called to discover
    newer upstream versions.
    """
    strategy = parameters.strategy if parameters else SyncStrategy.MINIMAL
    assert action.plugin_target is not None
    assert action.package is not None

    manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if manager is None:
        return SetupActionResult(action=action, success=True, message='PluginManager not available for query')

    try:
        installed = manager.installed_plugins()
        is_installed, detail, _matched = is_package_installed(action.package, installed)
    except Exception as e:
        logger.debug('Dry-run: could not check installed plugins for %s: %s', action.plugin_target.name, e)
        return SetupActionResult(action=action, success=True)

    if strategy == SyncStrategy.MINIMAL and is_installed:
        installed_ver = _matched.version if _matched else None

        # --- Update detection (opt-in) --------------------------------
        skip_reason = SkipReason.ALREADY_INSTALLED
        available_ver: str | None = None
        msg: str | None = detail

        detect = parameters is not None and parameters.detect_updates
        has_env = action.installer is not None and action.installer in environments

        if detect and has_env and action.installer is not None and parameters is not None:
            env = environments[action.installer]
            newer = _check_for_newer_version(
                env,
                action.package,
                installed_ver,
                include_prereleases=action.include_prereleases,
            )
            if newer is not None:
                skip_reason = SkipReason.UPDATE_AVAILABLE
                available_ver = newer
                msg = f'{action.package.name} {installed_ver} \u2192 {available_ver}'
                logger.info(f"Dry-run: update available for plugin '{action.package}': {msg}")
            else:
                logger.info("Dry-run: skipping plugin '%s': %s", action.package, detail)
        else:
            logger.info("Dry-run: skipping plugin '%s': %s", action.package, detail)

        return SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=skip_reason,
            message=msg,
            installed_version=installed_ver,
            available_version=available_ver,
        )
    if strategy != SyncStrategy.MINIMAL and not is_installed:
        return SetupActionResult(action=action, success=True, message='not installed, will install instead')

    return SetupActionResult(action=action, success=True)


def _check_for_newer_version(
    env: Environment,
    package: PackageRef,
    installed_version: str | None,
    *,
    include_prereleases: bool = False,
) -> str | None:
    """Query the plugin for a newer upstream version.

    Returns the latest version string when a newer version exists,
    or ``None`` when the installed version is already the latest
    (or when the plugin does not support update checks).
    """
    try:
        updates = env.check_updates(
            CheckUpdatesParameters(
                packages=[package],
                include_prereleases=include_prereleases,
            )
        )
    except Exception as e:
        logger.debug('check_updates failed for %s via %s: %s', package, env.tool_name(), e)
        return None

    if not updates:
        return None

    latest = updates[0]
    if latest.version is None:
        return None

    # Compare versions when possible to avoid false positives
    if installed_version is not None:
        try:
            if Version(latest.version) <= Version(installed_version):
                return None
        except InvalidVersion:
            # Non-PEP-440 versions — fall back to string inequality
            if latest.version == installed_version:
                return None

    return latest.version


def is_package_installed(
    package: PackageRef,
    installed_packages: list[Package],
    name_validator: str | None = None,
    kind: PluginKind | None = None,
) -> tuple[bool, str | None, Package | None]:
    """Checks if a package is already installed with a compatible version.

    When *name_validator* is `'pep440'`, uses PEP 440 canonicalization
    and specifier matching.  Otherwise, uses case-insensitive name
    comparison and simple string version equality.

    For `RUNTIME` actions, name comparison uses prefix matching so
    that a request for `3.14` matches an installed `3.14-64`
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
