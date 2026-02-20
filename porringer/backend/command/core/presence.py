"""Presence detection and dry-run simulation.

Checks whether packages are already installed so the sync engine can
skip redundant operations.
"""

import asyncio
import logging
from pathlib import Path

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SyncStrategy,
)

from .resolution import ResolutionContext, is_package_installed, resolve_operation, resolved_to_result

# Re-export for backward compatibility with existing callers
__all__ = ['async_dry_run_action', 'dry_run_action', 'is_package_installed']

logger = logging.getLogger(__name__)


async def async_dry_run_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_path: Path | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate executing an action in dry-run mode (async).

    For package/tool/runtime actions, delegates to
    :func:`resolve_operation` to determine what *would* happen, then
    maps the result to a ``SetupActionResult`` via
    :func:`resolved_to_result`.

    Post-sync commands (``kind is None``) always report success since
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
            return await _async_dry_run_package_action(
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


def dry_run_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_path: Path | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate executing an action in dry-run mode (sync wrapper).

    Thin synchronous façade over :func:`async_dry_run_action` for
    callers that are not in an async context.  Prefer the async
    variant when running inside the execution pipeline.

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
    return asyncio.run(
        async_dry_run_action(
            action,
            environments,
            project_path=project_path,
            project_environments=project_environments,
            parameters=parameters,
        )
    )


async def _async_dry_run_package_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    project_path: Path | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate a package or plugin action in dry-run mode.

    Delegates to :func:`resolve_operation` for the install/upgrade/skip
    decision and maps the result via :func:`resolved_to_result`.
    """
    strategy = parameters.strategy if parameters else SyncStrategy.MINIMAL
    detect_updates = parameters.detect_updates if parameters else False

    if action.installer is None or action.package is None:
        return SetupActionResult(action=action, success=True)

    ctx = ResolutionContext(
        project_path=project_path,
        project_environments=project_environments,
        detect_updates=detect_updates,
    )

    resolved = await resolve_operation(action, environments, strategy, ctx)

    if resolved.operation.name == 'SKIP':
        logger.info("Dry-run: skipping '%s': %s", action.package, resolved.message)

    return resolved_to_result(resolved)
