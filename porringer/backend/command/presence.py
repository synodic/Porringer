"""Presence detection and dry-run simulation.

Checks whether packages are already installed so the sync engine can
skip redundant operations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SkipReason,
    SyncStrategy,
)

logger = logging.getLogger(__name__)


def dry_run_action(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    *,
    project_path: Path | None = None,
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
        strategy: The sync strategy (affects skip logic for packages).
        project_path: Optional project directory for scoped package queries.

    Returns:
        The simulated result.
    """
    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            return _dry_run_package_action(action, environments, strategy, project_path=project_path)
        case PluginKind.PROJECT | PluginKind.SCM | None:
            return SetupActionResult(action=action, success=True)
        case _:
            return SetupActionResult(action=action, success=False, message=f'Unknown action kind: {action.kind}')


def _dry_run_package_action(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    *,
    project_path: Path | None = None,
) -> SetupActionResult:
    """Simulate a package action in dry-run mode."""
    if action.installer is None or action.package is None or action.installer not in environments:
        return SetupActionResult(action=action, success=True)

    # Determine name validator from the plugin
    env = environments[action.installer]
    validator = type(env).package_name_validator()

    try:
        installed_packages = environments[action.installer].packages(project_path=project_path)
        is_installed, installed_detail = is_package_installed(
            action.package, installed_packages, validator, action.kind
        )
    except Exception as e:
        logger.debug(f'Dry-run: could not check installed packages for {action.installer}: {e}')
        return SetupActionResult(action=action, success=True)

    if strategy == SyncStrategy.MINIMAL:
        if is_installed:
            logger.info(f"Dry-run: skipping '{action.package}': {installed_detail}")
            return SetupActionResult(
                action=action,
                success=True,
                skipped=True,
                skip_reason=SkipReason.ALREADY_INSTALLED,
                message=installed_detail,
            )
    elif not is_installed:
        return SetupActionResult(
            action=action,
            success=True,
            message='not installed, will install instead',
        )

    return SetupActionResult(action=action, success=True)


def is_package_installed(
    package: PackageRef,
    installed_packages: list[Package],
    name_validator: str | None = None,
    kind: PluginKind | None = None,
) -> tuple[bool, str | None]:
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
        Tuple of (is_installed, skip_reason or None)
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
            return True, f'{installed.name}=={installed.version} already installed'

        if installed.version is not None:
            if is_pep440:
                try:
                    req = Requirement(str(package))
                    if Version(installed.version) in req.specifier:
                        return True, f'{installed.name}=={installed.version} satisfies {package}'
                except InvalidVersion, InvalidRequirement:
                    pass
            else:
                # Non-PEP-440: installed means installed; constraint
                # satisfaction is left to the underlying tool.
                return True, f'{installed.name}=={installed.version} already installed'

    return False, None
