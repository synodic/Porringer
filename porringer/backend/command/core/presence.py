"""Presence detection and dry-run simulation.

Checks whether packages are already installed so the sync engine can
skip redundant operations.
"""

import logging
from pathlib import Path

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from porringer.schema.execution import CloneStatus, CloneStatusKind

from .resolution import ResolutionContext, resolve_operation, resolved_to_result

__all__ = ['dry_run_action', 'clone_status_to_result']

logger = logging.getLogger(__name__)


async def dry_run_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    context: ResolutionContext | None = None,
    scm_environments: dict[str, ScmEnvironment] | None = None,
    working_dir: Path | None = None,
    parameters: SetupParameters | None = None,
) -> SetupActionResult:
    """Simulate executing an action in dry-run mode (async).

    For package/tool/runtime actions, delegates to
    :func:`resolve_operation` to determine what *would* happen, then
    maps the result to a ``SetupActionResult`` via
    :func:`resolved_to_result`.

    For SCM actions, performs a lightweight ``is_cloned()`` check
    when SCM environments and a working directory are provided.

    Post-sync commands (``kind is None``) always report success since
    they would unconditionally run during a real execution.

    Args:
        action: The action to simulate.
        environments: Dict of instantiated environment plugins.
        context: Optional resolution context providing runtime paths,
            project-environment references, HTTP client, and package
            cache for presence detection.
        scm_environments: Optional dict of SCM-environment plugins,
            used for SCM clone presence detection.
        working_dir: Working directory (manifest location) for SCM checks.
        parameters: Full setup parameters.  When provided, ``strategy``
            and ``detect_updates`` are read from it.  When ``None``,
            ``SyncStrategy.MINIMAL`` is used.

    Returns:
        The simulated result.
    """
    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            return await _dry_run_package_action(
                action,
                environments,
                context=context,
                parameters=parameters,
            )
        case PluginKind.SCM:
            return await _dry_run_scm_action(
                action,
                scm_environments=scm_environments,
                working_dir=working_dir,
            )
        case PluginKind.PROJECT | None:
            return SetupActionResult(action=action, success=True)
        case _:
            return SetupActionResult(action=action, success=False, message=f'Unknown action kind: {action.kind}')


def clone_status_to_result(
    action: SetupAction,
    clone_status: CloneStatus,
    url: str,
    destination: Path,
) -> SetupActionResult | None:
    """Map a ``CloneStatus`` to a skip result, or ``None`` for MISSING.

    Shared by the dry-run presence path and the real execution path
    to produce consistent skip results for already-cloned and
    URL-mismatch cases.

    Args:
        action: The action being checked.
        clone_status: Result from ``ScmEnvironment.is_cloned()``.
        url: The manifest URL being checked.
        destination: The target clone destination.

    Returns:
        A ``SetupActionResult`` with ``skipped=True`` when the repo is
        present, or ``None`` when the repository is missing and cloning
        should proceed.
    """
    match clone_status.kind:
        case CloneStatusKind.CLONED:
            actual_url = clone_status.remote_url or url
            remote_info = f' (matched remote: {clone_status.matched_remote})' if clone_status.matched_remote else ''
            message = f"Already cloned at '{destination}' (remote: {actual_url}){remote_info}"
            logger.info(message)
            return SetupActionResult(
                action=action,
                success=True,
                skipped=True,
                skip_reason=SkipReason.ALREADY_INSTALLED,
                message=message,
            )
        case CloneStatusKind.URL_MISMATCH:
            actual = clone_status.remote_url or '(unknown)'
            message = (
                f"Repository exists at '{destination}' but no remote matches '{url}'. "
                f"Found remote: '{actual}'. May be a fork workflow."
            )
            logger.warning(message)
            return SetupActionResult(
                action=action,
                success=True,
                skipped=True,
                skip_reason=SkipReason.ALREADY_INSTALLED,
                message=message,
            )
        case _:
            return None


async def _dry_run_scm_action(
    action: SetupAction,
    *,
    scm_environments: dict[str, ScmEnvironment] | None = None,
    working_dir: Path | None = None,
) -> SetupActionResult:
    """Simulate an SCM clone action in dry-run mode.

    Performs a lightweight ``is_cloned()`` check to determine whether
    the repository already exists at the destination.  Returns a
    skip result when already cloned or when a URL mismatch is detected
    (fork workflow).
    """
    scm_envs = scm_environments or {}
    if (
        not scm_envs
        or action.installer is None
        or action.installer not in scm_envs
        or action.package is None
        or working_dir is None
    ):
        # Cannot perform presence check — report as "would succeed"
        return SetupActionResult(action=action, success=True)

    scm_env = scm_envs[action.installer]
    url = action.package.name

    clone_status = await scm_env.is_cloned(url, working_dir)

    return clone_status_to_result(action, clone_status, url, working_dir) or SetupActionResult(
        action=action, success=True
    )


async def _dry_run_package_action(
    action: SetupAction,
    environments: dict[str, Environment],
    *,
    context: ResolutionContext | None = None,
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

    ctx = context or ResolutionContext()
    # Merge strategy-derived fields into the context
    ctx = ResolutionContext(
        project_path=ctx.project_path,
        project_environments=ctx.project_environments,
        detect_updates=detect_updates,
        http_client=ctx.http_client,
        package_cache=ctx.package_cache,
        runtime_context=ctx.runtime_context,
    )

    resolved = await resolve_operation(action, environments, strategy, ctx)

    if resolved.operation.name == 'SKIP':
        logger.info("Dry-run: skipping '%s': %s", action.package, resolved.message)

    return resolved_to_result(resolved)
