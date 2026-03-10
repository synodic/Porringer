"""Phased execution engine.

Orchestrates the multi-phase setup flow: runtime → packages → tools →
project-sync → SCM → post-sync commands.  Each phase ensures its
prerequisites are met before proceeding.
"""

import asyncio
import logging
import os
import sysconfig
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx

from porringer.backend.backend import BackendResolver
from porringer.core.path import ensure_system_path, reset_sync_state
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.plugin_manager import (
    PluginManager,
    find_plugin_manager,
)
from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
)
from porringer.core.plugin_schema.runtime import RuntimeContext, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Ecosystem, Package, PluginKind
from porringer.schema import (
    Install,
    InstallReason,
    ManifestMetadata,
    Operation,
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    Skip,
    SkipReason,
    SubActionProgress,
    SyncStrategy,
    Uninstall,
    Upgrade,
)
from porringer.utility.exception import PluginError
from porringer.utility.utility import StreamProgress, stream_command

from .action_builder import (
    PHASE_ORDER,
    STRATEGY_VERB,
    action_description,
    get_cli_command,
)
from .discovery import (
    DiscoveredPlugins,
    discover_all_plugins,
    invalidate_plugin_cache,
)
from .phase import run_phases
from .presence import clone_status_to_result, dry_run_action
from .resolution import (
    PackageCache,
    ResolutionContext,
    resolve_operation,
    resolve_uninstall_operation,
    resolved_to_result,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionState:
    """Mutable state for a single phased execution run.

    Owns the discovered plugin container, grouped phase actions, and
    common parameters that every phase needs.  Provides
    :meth:`refresh_all_plugins` and :meth:`propagate_runtime` as
    methods so callers don't need to pass a half-dozen arguments.

    Metadata fields (``manifest_path``, ``metadata``, ``preferences``)
    are accessed via the stored :attr:`preview`.
    """

    actions: list[SetupAction]
    phases: dict[PluginKind | None, list[SetupAction]]
    plugins: DiscoveredPlugins
    parameters: SetupParameters
    event_queue: asyncio.Queue[ProgressEvent | None]
    manifest_directory: Path
    preview: SetupResults
    results: list[SetupActionResult] = field(default_factory=list)
    runtime_context: RuntimeContext = field(default_factory=RuntimeContext)
    """Accumulated runtime context for this execution run.  Populated
    by :meth:`propagate_runtime` and threaded through to every
    operation that needs an interpreter path."""

    # -- convenience accessors (delegate to plugins / preview) ---------

    @property
    def environments(self) -> dict[str, Environment]:
        """Environment plugins from the current discovery."""
        return self.plugins.environments

    @property
    def project_environments(self) -> dict[str, ProjectEnvironment] | None:
        """Project-environment plugins (may be empty dict)."""
        return self.plugins.project_environments or None

    @property
    def scm_environments(self) -> dict[str, ScmEnvironment] | None:
        """SCM-environment plugins (may be empty dict)."""
        return self.plugins.scm_environments or None

    @property
    def manifest_path(self) -> Path | None:
        """Path to the manifest file (from preview)."""
        return self.preview.manifest_path

    @property
    def metadata(self) -> ManifestMetadata | None:
        """Display metadata from the manifest."""
        return self.preview.metadata

    @property
    def preferences(self) -> dict[Ecosystem, str]:
        """Ecosystem → plugin-name preferences from the manifest."""
        return dict(self.preview.preferences)

    @property
    def skip_project(self) -> bool:
        """Whether project-sync actions should be skipped."""
        return self.parameters.project_directory is False

    @property
    def fallback_dir(self) -> Path:
        """Working directory for SCM and post-sync command phases."""
        return determine_fallback_dir(self.parameters, self.manifest_directory)

    # -- convenience properties ----------------------------------------

    @property
    def strategy(self) -> SyncStrategy:
        """The sync strategy from the current parameters."""
        return self.parameters.strategy

    # -- plugin refresh machinery --------------------------------------

    def refresh_all_plugins(self) -> None:
        """Refresh PATH and re-discover all plugin types.

        Invalidates the plugin cache, re-discovers environments,
        project environments, and SCM environments.

        ``discover_all_plugins()`` always returns fresh instances
        (scan metadata is cached, instances are not), and ``.copy()``
        mints another fresh set for this execution state.
        ``runtime_context`` is *not* baked into plugin instances —
        it is threaded explicitly through every operation.
        """
        refresh_path()
        invalidate_plugin_cache()
        self.plugins = discover_all_plugins().copy()

    async def propagate_runtime(self) -> None:
        """Resolve interpreter paths from completed runtime actions.

        Finds the first ``RuntimeProvider`` among the RUNTIME-phase
        actions, resolves its executable, injects the directory onto
        ``PATH``, and stores the result in :attr:`runtime_context`
        so it can be threaded through subsequent operations.

        Plugin instances are **not** mutated.
        """
        result = await _propagate_runtime(
            self.phases[PluginKind.RUNTIME],
            self.plugins,
        )
        if result is not None:
            kind, executable = result
            self.runtime_context.executables[kind] = executable

    # -- result helpers ------------------------------------------------

    def emit(self, event: ProgressEvent) -> None:
        """Put *event* on the event queue."""
        self.event_queue.put_nowait(event)

    @property
    def resolution_context(self) -> ResolutionContext:
        """Resolution context for this execution run.

        Bundles the project-environment references and runtime context
        that flow through every resolution and execution helper.
        """
        return ResolutionContext(
            project_environments=self.project_environments,
            runtime_context=self.runtime_context,
        )

    # -- phase executor delegates --------------------------------------

    async def run_package_actions(self, actions: list[SetupAction]) -> tuple[list[SetupActionResult], bool]:
        """Execute package/tool/runtime actions.

        Returns:
            Tuple of (results, should_continue).
        """
        return await execute_package_actions(
            actions,
            self.environments,
            self.parameters,
            self.event_queue,
            self.resolution_context,
        )

    async def run_project_phase(self, actions: list[SetupAction]) -> list[SetupActionResult]:
        """Execute or skip project sync actions."""
        return await handle_project_phase(actions, self)

    async def run_scm_actions(self, actions: list[SetupAction]) -> list[SetupActionResult]:
        """Execute SCM clone actions."""
        return await _execute_scm_actions(
            actions,
            self.scm_environments,
            self.fallback_dir,
            self.parameters,
            self.event_queue,
        )

    async def run_command_actions(self, actions: list[SetupAction]) -> list[SetupActionResult]:
        """Execute post-sync shell commands."""
        return await execute_command_actions(actions, self)

    def resolve_deferred(self, actions: list[SetupAction]) -> None:
        """Resolve deferred actions and update their CLI commands."""
        resolve_deferred_actions(
            actions,
            self.plugins,
            self.strategy,
            preferences=self.preferences,
            runtime_context=self.runtime_context,
        )
        for action in actions:
            if action.cli_command is None or action.installer is not None:
                action.cli_command = get_cli_command(
                    action,
                    self.plugins,
                    self.strategy,
                )

    def early_return(self) -> SetupResults:
        """Create a ``SetupResults`` from the results accumulated so far."""
        return SetupResults(
            actions=self.actions,
            results=self.results,
            manifest_path=self.manifest_path,
            root_directory=self.manifest_directory,
            metadata=self.metadata,
            preferences=self.preview.preferences,
        )


# ---------------------------------------------------------------------------
# PATH refresh
# ---------------------------------------------------------------------------

_path_lock = threading.Lock()


def _prepend_to_path(dirs: list[str], *, require_exists: bool = False) -> None:
    """Prepend directories to ``os.environ['PATH']`` if not already present.

    Mutations are **process-global** by design — ``shutil.which()``
    and ``asyncio.create_subprocess_exec()`` inherit the process
    environment, so every concurrent ``execute_single()`` run
    benefits from directories added by earlier runtime resolution.
    Additions are idempotent and append-only (directories already
    present are skipped).

    Uses a lock to prevent concurrent mutations from interleaving.

    Args:
        dirs: Directory paths to prepend (in order).
        require_exists: When ``True``, skip directories that do not
            exist on disk.  Useful for ``sysconfig`` directories that
            may not have been created yet.
    """
    with _path_lock:
        current_path = os.environ.get('PATH', '')
        current_entries = set(current_path.split(os.pathsep))
        new_entries = [d for d in dirs if d not in current_entries and (not require_exists or Path(d).is_dir())]
        if new_entries:
            os.environ['PATH'] = os.pathsep.join(new_entries) + os.pathsep + current_path
            logger.debug('PATH updated with: %s', ', '.join(new_entries))


def refresh_path() -> None:
    """Prepend common script/binary directories to ``PATH``.

    First, re-synchronizes the process ``PATH`` with the operating
    system (e.g. registry on Windows) so that tools installed during
    the current session become visible.  Then prepends Python-specific
    ``sysconfig`` script directories so that executables such as
    ``pipx`` are discoverable after installation.
    """
    # Allow re-reading the OS PATH (handles tools installed mid-session).
    reset_sync_state()
    ensure_system_path()

    dirs: list[str] = []

    scripts_dir = sysconfig.get_path('scripts')
    if scripts_dir:
        dirs.append(scripts_dir)

    user_scripts = sysconfig.get_path('scripts', 'posix_user' if os.name != 'nt' else 'nt_user')
    if user_scripts:
        dirs.append(user_scripts)

    _prepend_to_path(dirs, require_exists=True)


def inject_runtime_path(executable: Path) -> None:
    """Prepend a resolved runtime's directories to ``PATH``.

    Makes the interpreter's directory and its platform-appropriate
    scripts sibling (``Scripts/`` on Windows, ``bin/`` on Unix)
    available to ``shutil.which()`` so that ``python`` and ``pip``
    are discoverable during the next plugin re-discovery.

    Args:
        executable: Absolute path to the interpreter.
    """
    parent = str(executable.parent)
    scripts = str(executable.parent / ('Scripts' if os.name == 'nt' else 'bin'))
    _prepend_to_path([parent, scripts])


# ---------------------------------------------------------------------------
# Post-sync command execution
# ---------------------------------------------------------------------------


async def execute_run_command(
    action: SetupAction,
    working_dir: Path,
    timeout: int,
    event_queue: asyncio.Queue[ProgressEvent | None],
) -> SetupActionResult:
    """Execute a post-install command with real-time output streaming.

    Uses ``stream_command`` so the event loop is never blocked, and
    streams stdout/stderr line-by-line as ``SUB_ACTION_PROGRESS``
    events on the *event_queue*.

    Args:
        action: The command action.
        working_dir: Working directory for the command.
        timeout: Timeout in seconds.
        event_queue: Queue to emit sub-action progress into.

    Returns:
        The result of the command execution.
    """
    if action.command is None or len(action.command) == 0:
        return SetupActionResult(action=action, success=False, message='No command specified')

    logger.info(f'Running command: {" ".join(action.command)}')

    def _progress_cb(update: SubActionProgress) -> None:
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.SUB_ACTION_PROGRESS,
                action=action,
                sub_action=update,
            )
        )

    progress = StreamProgress(
        action=action,
        callback=_progress_cb,
        phase='command',
    )

    try:
        result = await stream_command(
            action.command,
            progress=progress,
            cwd=working_dir,
            timeout=float(timeout),
        )
        if result.returncode == 0:
            return SetupActionResult(action=action, success=True)
        stderr = result.stderr.strip() if result.stderr else 'Unknown error'
        return SetupActionResult(
            action=action,
            success=False,
            message=f'Exit code {result.returncode}: {stderr}',
        )
    except TimeoutError:
        message = f'Command timed out after {timeout} seconds'
        logger.error(message)
        return SetupActionResult(action=action, success=False, message=message)
    except Exception as e:
        message = f'Command not found: {action.command[0]}' if isinstance(e, FileNotFoundError) else str(e)
        return SetupActionResult(action=action, success=False, message=message)


# ---------------------------------------------------------------------------
# Package execution helpers
# ---------------------------------------------------------------------------


async def resolve_runtime_tag_override(
    tag: str,
    ecosystem: str | None,
    environments: dict[str, Environment],
) -> RuntimeContext | None:
    """Build a one-off :class:`RuntimeContext` for a specific *tag*.

    Scans *environments* for a :class:`RuntimeProvider` whose
    ``provided_runtime_kind()`` matches *ecosystem*, then resolves
    *tag* to a concrete executable path.

    Returns ``None`` when no matching provider exists or when the tag
    cannot be resolved to an executable.
    """
    if ecosystem is None:
        return None

    for env in environments.values():
        if isinstance(env, RuntimeProvider) and env.provided_runtime_kind() == ecosystem:
            executable = await env.resolve_executable(tag)
            if executable is not None:
                return RuntimeContext(executables={ecosystem: executable})
            return None

    return None


async def execute_package(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    event_queue: asyncio.Queue[ProgressEvent | None],
    context: ResolutionContext | None = None,
    *,
    package_cache: PackageCache | None = None,
) -> SetupActionResult:
    """Execute a package install or upgrade based on the strategy.

    Delegates to :func:`resolve_operation` to determine the correct
    operation (install, upgrade, or skip), then dispatches to the
    appropriate execution helper.

    Args:
        action: The package action.
        environments: Dict of instantiated environment plugins.
        strategy: The sync strategy.
        event_queue: Queue to emit sub-action events into.
        context: Optional resolution context providing runtime paths,
            project-environment references, and package cache.
        package_cache: Optional shared cache for ``packages()`` results.
            When provided, presence checks share a single query per
            installer.  Invalidated after successful installs/upgrades
            so subsequent actions see fresh state.

    Returns:
        The result of the operation.
    """
    if action.installer is None or action.package is None:
        return SetupActionResult(action=action, success=False, message='Installer or package not specified')

    ctx = context or ResolutionContext()
    # Merge caller-provided cache into the context for resolution
    if package_cache is not None:
        ctx = ResolutionContext(
            project_path=ctx.project_path,
            project_environments=ctx.project_environments,
            http_client=ctx.http_client,
            package_cache=package_cache,
            runtime_context=ctx.runtime_context,
        )

    # --- Per-action runtime override --------------------------------------
    if action.runtime_tag is not None:
        override_ctx = await resolve_runtime_tag_override(action.runtime_tag, action.ecosystem, environments)
        if override_ctx is None:
            return SetupActionResult(
                action=action,
                success=False,
                message=(f"Could not resolve runtime tag '{action.runtime_tag}' for ecosystem '{action.ecosystem}'"),
            )
        ctx = ResolutionContext(
            project_path=ctx.project_path,
            project_environments=ctx.project_environments,
            http_client=ctx.http_client,
            package_cache=ctx.package_cache,
            runtime_context=override_ctx,
        )

    resolved = await resolve_operation(
        action,
        environments,
        strategy,
        ctx,
    )

    # --- Skip -------------------------------------------------------------
    if isinstance(resolved.operation, Skip):
        logger.info("Skipping '%s': %s", action.package, resolved.message)
        return resolved_to_result(resolved)

    # --- Plugin-management actions ----------------------------------------
    if action.plugin_target is not None:
        return await _attempt_plugin_operation(
            action,
            operation=resolved.operation,
            event_queue=event_queue,
            plugin_manager=resolved.plugin_manager,
            project_environments=ctx.project_environments,
        )

    # --- Normal package actions -------------------------------------------
    if action.installer not in environments:
        msg = f"Installer '{action.installer}' is not available"
        return SetupActionResult(action=action, success=False, message=msg)

    environment = environments[action.installer]
    match resolved.operation:
        case Install(reason=InstallReason.ENSURE_EXTRAS):
            verb = 'Ensuring'
        case Install():
            verb = 'Installing'
        case _:
            verb = 'Upgrading'
    logger.info(f"{verb} '{action.package}' via {action.installer}")
    return await _attempt_package_operation(
        action, environment, resolved.operation, event_queue, runtime_context=ctx.runtime_context
    )


async def _attempt_package_operation(
    action: SetupAction,
    environment: Environment,
    operation: Operation,
    event_queue: asyncio.Queue[ProgressEvent | None],
    *,
    runtime_context: RuntimeContext | None = None,
) -> SetupActionResult:
    """Attempt to install or upgrade a package via the given environment plugin.

    Args:
        action: The package action.
        environment: The environment plugin to use.
        operation: The resolved operation (Install or Upgrade).
        event_queue: Queue to emit sub-action events into.
        runtime_context: Resolved runtime paths for this execution run.

    Returns:
        The result of the attempt.
    """
    if isinstance(operation, Install):
        execute: Callable[[PackageParameters], Awaitable[Package | None]] = environment.install
        verb, verb_past = 'install', 'Installed'
    else:
        execute = environment.upgrade
        verb, verb_past = 'upgrade', 'Upgraded'

    return await _attempt_operation(
        action,
        spec=OperationSpec(
            execute=execute,
            verb=verb,
            verb_past=verb_past,
        ),
        event_queue=event_queue,
        runtime_context=runtime_context,
    )


async def execute_uninstall(
    action: SetupAction,
    environments: dict[str, Environment],
    event_queue: asyncio.Queue[ProgressEvent | None],
    context: ResolutionContext | None = None,
    *,
    package_cache: PackageCache | None = None,
) -> SetupActionResult:
    """Execute a package uninstall after resolving presence.

    Delegates to :func:`resolve_uninstall_operation` to determine
    whether the package is installed, then dispatches to
    ``uninstall`` (or ``plugin_remove`` for plugin-target
    actions).

    Args:
        action: The package action describing what to uninstall.
        environments: Dict of instantiated environment plugins.
        event_queue: Queue to emit sub-action events into.
        context: Optional resolution context providing runtime paths,
            project-environment references, and package cache.
        package_cache: Optional shared cache for ``packages()`` results.

    Returns:
        The result of the operation.
    """
    if action.installer is None or action.package is None:
        return SetupActionResult(action=action, success=False, message='Installer or package not specified')

    ctx = context or ResolutionContext()
    # Merge caller-provided cache into the context for resolution
    if package_cache is not None:
        ctx = ResolutionContext(
            project_path=ctx.project_path,
            project_environments=ctx.project_environments,
            http_client=ctx.http_client,
            package_cache=package_cache,
            runtime_context=ctx.runtime_context,
        )

    resolved = await resolve_uninstall_operation(
        action,
        environments,
        ctx,
    )

    # --- Skip (not installed) ---------------------------------------------
    if isinstance(resolved.operation, Skip):
        logger.info("Skipping uninstall of '%s': %s", action.package, resolved.message)
        return resolved_to_result(resolved)

    # --- Plugin-management actions ----------------------------------------
    if action.plugin_target is not None:
        return await _attempt_plugin_operation(
            action,
            operation=Uninstall(),
            event_queue=event_queue,
            plugin_manager=resolved.plugin_manager,
            project_environments=ctx.project_environments,
        )

    # --- Normal package actions -------------------------------------------
    environment = environments[action.installer]
    logger.info("Uninstalling '%s' via %s", action.package, action.installer)
    return await _attempt_operation(
        action,
        spec=OperationSpec(
            execute=environment.uninstall,
            verb='uninstall',
            verb_past='Uninstalled',
        ),
        event_queue=event_queue,
        runtime_context=ctx.runtime_context,
    )


async def _attempt_plugin_operation(
    action: SetupAction,
    *,
    operation: Operation,
    event_queue: asyncio.Queue[ProgressEvent | None],
    plugin_manager: PluginManager | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> SetupActionResult:
    """Add, update, or remove a plugin via its native ``PluginManager``.

    Uses the *plugin_manager* resolved during operation resolution
    when available, falling back to a fresh lookup when not provided.

    Args:
        action: The plugin action (``plugin_target`` must be set).
        operation: The resolved operation (Install, Upgrade, or
            Uninstall).
        event_queue: Queue to emit sub-action events into.
        plugin_manager: Pre-resolved ``PluginManager`` from
            :func:`resolve_operation`, if available.
        project_environments: Dict of project-environment plugins,
            used as fallback when *plugin_manager* is ``None``.

    Returns:
        The result of the attempt.
    """
    assert action.plugin_target is not None
    assert action.package is not None

    if plugin_manager is None:
        plugin_manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if plugin_manager is None:
        msg = f"No PluginManager found for '{action.plugin_target.name}'"
        return SetupActionResult(action=action, success=False, message=msg)

    match operation:
        case Install():
            execute = plugin_manager.plugin_add
            verb, verb_past, suffix = 'add plugin', 'Added', f' to {action.plugin_target.name} (native)'
        case Upgrade():
            execute = plugin_manager.plugin_update
            verb, verb_past, suffix = 'update plugin', 'Updated', f' to {action.plugin_target.name} (native)'
        case Uninstall():
            execute = plugin_manager.plugin_remove
            verb, verb_past, suffix = 'remove plugin', 'Removed', f' from {action.plugin_target.name} (native)'
        case _:
            msg = f'Unexpected operation {operation} for plugin action'
            return SetupActionResult(action=action, success=False, message=msg)

    logger.info(
        "Using native plugin management (%s) for '%s' via %s",
        verb,
        action.plugin_target.name,
        type(plugin_manager).__name__,
    )
    return await _attempt_operation(
        action,
        spec=OperationSpec(
            execute=execute,
            verb=verb,
            verb_past=verb_past,
            success_suffix=suffix,
        ),
        event_queue=event_queue,
    )


@dataclass(frozen=True)
class OperationSpec:
    """Specification for a single package operation.

    Bundles the async callable together with the human-readable verb
    forms used in log / result messages.
    """

    execute: Callable[[PackageParameters], Awaitable[Package | None]]
    verb: str
    verb_past: str
    success_suffix: str = ''


async def _attempt_operation(
    action: SetupAction,
    *,
    spec: OperationSpec,
    event_queue: asyncio.Queue[ProgressEvent | None],
    runtime_context: RuntimeContext | None = None,
) -> SetupActionResult:
    """Core helper that runs an async package operation with standard error handling.

    Builds the progress callback, constructs `PackageParameters`,
    calls *spec.execute*, and catches the standard exception set.

    Args:
        action: The action being executed.
        spec: The operation specification (callable + verb forms).
        event_queue: Queue to emit sub-action events into.
        runtime_context: Resolved runtime paths for this execution run.

    Returns:
        The result of the attempt.
    """
    success = False
    message = ''

    def sub_action_cb(update: SubActionProgress) -> None:
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.SUB_ACTION_PROGRESS,
                action=action,
                sub_action=update,
            )
        )

    try:
        if action.package is None:
            return SetupActionResult(action=action, success=False, message='No package specified')
        params = PackageParameters(
            package=action.package,
            dry=False,
            include_prereleases=action.include_prereleases,
            progress_callback=sub_action_cb,
            runtime_context=runtime_context,
        )
        result = await spec.execute(params)

        if result is not None:
            success = True
            message = f'{spec.verb_past} {result.name}{spec.success_suffix}'
        else:
            message = f"Failed to {spec.verb} '{action.package}'{spec.success_suffix}"
    except PluginError as e:
        logger.error(f'Plugin error {spec.verb}ing {action.package}: {e}')
        message = str(e)
    except asyncio.CancelledError:
        logger.error(f'{spec.verb.capitalize()} cancelled for {action.package}')
        message = f'{spec.verb.capitalize()} cancelled'
    except TimeoutError as e:
        logger.error(f'Timeout {spec.verb}ing {action.package}: {e}')
        message = str(e)
    except Exception as e:
        message = str(e)

    return SetupActionResult(action=action, success=success, message=message)


# ---------------------------------------------------------------------------
# Parallel / sequential orchestration
# ---------------------------------------------------------------------------


async def execute_package_actions(
    package_actions: list[SetupAction],
    environments: dict[str, Environment],
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
    context: ResolutionContext | None = None,
) -> tuple[list[SetupActionResult], bool]:
    """Execute PACKAGE actions with parallel support.

    Returns:
        Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
    """
    if parameters.dry_run:
        return (
            await _dry_run_package_actions(
                package_actions,
                environments,
                event_queue,
                context=context,
                parameters=parameters,
            ),
            True,
        )

    parallel_actions, sequential_actions = _group_actions_by_parallelism(package_actions, environments)

    results: list[SetupActionResult] = []

    # Shared cache — presence checks share a single packages() call per
    # installer.  Invalidated by execute_package after successful mutations.
    cache = PackageCache()

    ctx = context or ResolutionContext()

    # Shared HTTP client — resolution now always checks for upstream
    # updates, so a pooled connection avoids per-action TCP overhead.
    async with httpx.AsyncClient(timeout=10.0) as shared_client:
        enriched = ResolutionContext(
            project_path=ctx.project_path,
            project_environments=ctx.project_environments,
            http_client=shared_client,
            package_cache=cache,
            runtime_context=ctx.runtime_context,
        )

        # Execute parallel actions concurrently
        if parallel_actions:
            parallel_results, should_continue = await _run_parallel_packages(
                parallel_actions,
                environments,
                parameters,
                event_queue,
                enriched,
                package_cache=cache,
            )
            results.extend(parallel_results)
            if not should_continue:
                return results, False

        # Execute sequential actions one at a time
        sequential_results, should_continue = await _run_sequential_packages(
            sequential_actions,
            environments,
            parameters,
            event_queue,
            enriched,
            package_cache=cache,
        )
        results.extend(sequential_results)

    return results, should_continue


async def _dry_run_package_actions(
    package_actions: list[SetupAction],
    environments: dict[str, Environment],
    event_queue: asyncio.Queue[ProgressEvent | None],
    *,
    context: ResolutionContext | None = None,
    parameters: SetupParameters | None = None,
) -> list[SetupActionResult]:
    """Execute dry-run for package actions in parallel.

    All actions are dispatched concurrently via ``asyncio.TaskGroup``.
    A shared :class:`PackageCache` ensures each installer's
    ``packages()`` is called at most once, regardless of how many
    actions target the same installer.

    Results are emitted in the original action order regardless of
    which checks finish first, preserving deterministic card ordering
    for GUI consumers.
    """
    ctx = context or ResolutionContext()
    max_concurrency = parameters.max_concurrency if parameters else 0

    result_slots: list[SetupActionResult | None] = [None] * len(package_actions)

    semaphore: asyncio.Semaphore | None = asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None

    # Shared cache — collapses N concurrent packages() calls per installer to 1
    cache = PackageCache()

    async def _check(index: int, action: SetupAction, client: httpx.AsyncClient) -> None:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            # Emit ACTION_STARTED *before* the check so GUI clients can
            # show a spinner while the dry-run is in progress.
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            try:
                result = await dry_run_action(
                    action,
                    environments,
                    context=ResolutionContext(
                        project_path=ctx.project_path,
                        project_environments=ctx.project_environments,
                        runtime_context=ctx.runtime_context,
                        http_client=client,
                        package_cache=cache,
                    ),
                    parameters=parameters,
                )
            except Exception as exc:
                logger.debug('Dry-run check failed for %s: %s', action.description, exc)
                result = SetupActionResult(action=action, success=False, message=str(exc))
            result_slots[index] = result
            event_queue.put_nowait(
                ProgressEvent(
                    kind=ProgressEventKind.ACTION_COMPLETED,
                    action=action,
                    result=result,
                )
            )
        finally:
            if semaphore is not None:
                semaphore.release()

    async with httpx.AsyncClient(timeout=10.0) as shared_client, asyncio.TaskGroup() as tg:
        for i, action in enumerate(package_actions):
            tg.create_task(_check(i, action, shared_client))

    # All tasks completed — return results in original order.
    # Replace any unfilled slots (e.g. from cancellation) with
    # explicit failure results so callers always get a 1:1 mapping.
    results: list[SetupActionResult] = []
    for i, maybe in enumerate(result_slots):
        if maybe is not None:
            results.append(maybe)
        else:
            results.append(
                SetupActionResult(
                    action=package_actions[i],
                    success=False,
                    message='Task did not complete',
                )
            )
    return results


def _group_actions_by_parallelism(
    install_actions: list[SetupAction],
    environments: dict[str, Environment],
) -> tuple[list[SetupAction], list[SetupAction]]:
    """Group actions into parallel and sequential based on plugin support."""
    parallel_actions: list[SetupAction] = []
    sequential_actions: list[SetupAction] = []

    for action in install_actions:
        supports = (
            action.installer and action.installer in environments and environments[action.installer].supports_parallel()
        )
        if supports:
            parallel_actions.append(action)
        else:
            sequential_actions.append(action)

    return parallel_actions, sequential_actions


async def _run_sequential_packages(
    sequential_actions: list[SetupAction],
    environments: dict[str, Environment],
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
    context: ResolutionContext | None = None,
    *,
    package_cache: PackageCache | None = None,
) -> tuple[list[SetupActionResult], bool]:
    """Run package actions sequentially."""
    ctx = context or ResolutionContext()
    results: list[SetupActionResult] = []
    for action in sequential_actions:
        event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
        result = await execute_package(
            action,
            environments,
            parameters.strategy,
            event_queue,
            context,
            package_cache=package_cache,
        )
        results.append(result)
        # Invalidate cache after successful install/upgrade so the next
        # action sees fresh state for the same installer.
        if result.success and not result.skipped and package_cache is not None and action.installer:
            package_cache.invalidate_packages(action.installer, ctx.project_path)
            if action.plugin_target is not None:
                package_cache.invalidate_plugins(action.plugin_target.name)
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.ACTION_COMPLETED,
                action=action,
                result=result,
            )
        )
        if not result.success and not result.skipped and parameters.fail_fast:
            logger.error(f'Action failed: {action.description} - {result.message}')
            return results, False
    return results, True


async def _run_parallel_packages(
    parallel_actions: list[SetupAction],
    environments: dict[str, Environment],
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
    context: ResolutionContext | None = None,
    *,
    package_cache: PackageCache | None = None,
) -> tuple[list[SetupActionResult], bool]:
    """Run package actions in parallel using TaskGroup.

    Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.
    All tasks are automatically cancelled if any raises an unhandled exception.

    Returns:
        Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
    """
    results: dict[int, SetupActionResult] = {}
    action_indices = {id(action): i for i, action in enumerate(parallel_actions)}
    max_concurrency = parameters.max_concurrency
    semaphore: asyncio.Semaphore | None = asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None

    async def package_with_event(action: SetupAction) -> None:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            try:
                result = await execute_package(
                    action,
                    environments,
                    parameters.strategy,
                    event_queue,
                    context,
                    package_cache=package_cache,
                )
            except Exception as e:
                result = SetupActionResult(action=action, success=False, message=str(e))
            event_queue.put_nowait(
                ProgressEvent(
                    kind=ProgressEventKind.ACTION_COMPLETED,
                    action=action,
                    result=result,
                )
            )
            results[action_indices[id(action)]] = result
        finally:
            if semaphore is not None:
                semaphore.release()

    try:
        async with asyncio.TaskGroup() as tg:
            for action in parallel_actions:
                tg.create_task(package_with_event(action))
    except ExceptionGroup as eg:
        # TaskGroup raises ExceptionGroup if any task fails with unhandled exception
        # Our package_with_event catches exceptions, so this shouldn't happen normally
        logger.error(f'Parallel package operation failed with exceptions: {eg.exceptions}')

    # Convert dict to ordered list
    result_list = [results.get(i) for i in range(len(parallel_actions))]
    final_results: list[SetupActionResult] = []

    for action, maybe_result in zip(parallel_actions, result_list, strict=False):
        action_result: SetupActionResult
        if maybe_result is None:
            # Task was cancelled before completing
            action_result = SetupActionResult(action=action, success=False, message='Task cancelled')
        else:
            action_result = maybe_result
        final_results.append(action_result)
        if not action_result.success and not action_result.skipped and parameters.fail_fast:
            logger.error(f'Action failed: {action.description} - {action_result.message}')
            return final_results, False

    return final_results, True


# ---------------------------------------------------------------------------
# Command / project / SCM action execution
# ---------------------------------------------------------------------------


async def execute_command_actions(
    command_actions: list[SetupAction],
    state: ExecutionState,
) -> list[SetupActionResult]:
    """Execute RUN_COMMAND actions sequentially."""
    results: list[SetupActionResult] = []
    for action in command_actions:
        state.event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        if state.parameters.dry_run:
            result = await dry_run_action(
                action,
                state.environments,
                parameters=state.parameters,
            )
        else:
            result = await execute_run_command(
                action,
                state.fallback_dir,
                state.parameters.timeout,
                event_queue=state.event_queue,
            )
        results.append(result)
        state.event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.ACTION_COMPLETED,
                action=action,
                result=result,
            )
        )
        if not result.success and not result.skipped:
            logger.error(f'Action failed: {action.description} - {result.message}')
            if state.parameters.fail_fast:
                break
    return results


def determine_fallback_dir(parameters: SetupParameters, root_directory: Path) -> Path:
    """Determine the fallback working directory for SCM and post-sync commands.

    Project-sync actions use per-plugin auto-discovery instead of
    this method.  This fallback is used by SCM clone and post-sync
    command phases only.

    Args:
        parameters: Setup parameters that may specify a project directory.
        root_directory: The logical root directory for this manifest.

    Returns:
        The working directory to use.
    """
    if isinstance(parameters.project_directory, Path):
        return parameters.project_directory
    return root_directory


async def handle_project_phase(
    project_actions: list[SetupAction],
    state: ExecutionState,
) -> list[SetupActionResult]:
    """Execute or skip project sync actions depending on context.

    Args:
        project_actions: The project-kind actions to process.
        state: Execution state with parameters, flags, and directories.

    Returns:
        Results for each project action.
    """
    if not state.skip_project:
        return await _execute_project_sync_actions(
            project_actions,
            state.project_environments,
            state.manifest_directory,
            state.parameters,
            state.event_queue,
            runtime_context=state.runtime_context,
        )
    return skip_actions(
        project_actions,
        SkipReason.NO_PROJECT_DIRECTORY,
        'No project directory provided',
        state.event_queue,
    )


# ---------------------------------------------------------------------------
# Phased execution core
# ---------------------------------------------------------------------------


def _plugins_discovered_event(
    plugins: DiscoveredPlugins,
    runtime_context: RuntimeContext | None = None,
) -> ProgressEvent:
    """Build a ``PLUGINS_DISCOVERED`` progress event.

    Collects availability for every discovered plugin and returns a
    single ``ProgressEvent`` that GUI clients can use to render
    availability badges.

    When *runtime_context* is provided, ``RuntimeConsumer`` plugins are
    probed via the runtime-aware ``query_availability()`` path.
    """
    all_plugins: dict[str, ToolBasedPlugin] = {
        **plugins.environments,
        **plugins.project_environments,
        **plugins.scm_environments,
    }
    plugin_availability = {name: plugin.query_availability(runtime_context) for name, plugin in all_plugins.items()}
    logger.info('Plugins discovered — availability: %s', plugin_availability)
    return ProgressEvent(
        kind=ProgressEventKind.PLUGINS_DISCOVERED,
        plugin_names=sorted(plugin_availability.keys()),
        plugin_availability=plugin_availability,
    )


async def execute_single(
    preview: SetupResults,
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
    *,
    plugins: DiscoveredPlugins | None = None,
) -> SetupResults:
    """Execute setup actions for a single path with parallel support.

    Execution is **phased** so that each layer's prerequisite tools
    are available before they are needed:

    1. **Runtime** — install/resolve language runtimes (pim, pyenv).
    2. **Package** — install packages into the current environment
       (pip, uv).  This may install tool prerequisites such as pipx.
    3. **Tool** — install isolated CLI tools (pipx).  Plugins are
       re-discovered after Phase 2 so that newly-installed backends
       are available.  Deferred actions whose ``installer`` was
       ``None`` at preview time are resolved here.
    4. **Project sync** — run ``pdm install`` / ``uv sync`` in the
       manifest directory.
    5. **SCM clone** — clone source-control repositories.
    6. **Post-sync commands** — run arbitrary shell commands.

    Args:
        preview: The parsed manifest preview containing actions,
            ``root_directory``, ``manifest_path``, and ``metadata``.
        parameters: The setup parameters.
        event_queue: Queue to emit ``ProgressEvent`` items into.
        plugins: Pre-discovered plugins.  When provided, plugin
            discovery is skipped entirely (useful when the caller has
            already discovered plugins for a batch of manifests).

    Returns:
        SetupResults containing the results of each action.
    """
    actions = preview.actions
    assert preview.root_directory is not None  # guaranteed by parse_manifest
    root_directory = preview.root_directory

    logger.info(f'Executing {len(actions)} setup actions async (dry_run={parameters.dry_run})')

    # Use pre-discovered plugins when available; otherwise discover.
    plugins_discovered_here = plugins is None
    if plugins is None:
        if not parameters.dry_run:
            invalidate_plugin_cache()
        plugins = discover_all_plugins(use_cache=parameters.dry_run)

    # Emit PLUGINS_DISCOVERED only when we performed discovery ourselves.
    # Batch callers (execute_stream / run) pre-discover and emit the
    # event once for the entire batch, so we skip it here to avoid
    # sending duplicate events.
    if plugins_discovered_here:
        event_queue.put_nowait(_plugins_discovered_event(plugins))

    state = ExecutionState(
        actions=actions,
        phases=group_actions_by_phase(actions),
        # .copy() builds fresh plugin instances (when factory metadata
        # is present) so that accidental state on a plugin object
        # cannot leak between runs or back to the caller.
        plugins=plugins.copy(),
        parameters=parameters,
        event_queue=event_queue,
        manifest_directory=root_directory,
        preview=preview,
    )

    # Populate CLI commands for all resolved actions
    _populate_cli_commands(actions, state)

    # Emit MANIFEST_LOADED — the fully-resolved preview with CLI commands.
    state.emit(
        ProgressEvent(
            kind=ProgressEventKind.MANIFEST_LOADED,
            manifest=preview,
        )
    )

    # Run all phases via the generalized phase loop.
    await run_phases(state)

    return SetupResults(
        actions=actions,
        results=state.results,
        manifest_path=state.manifest_path,
        root_directory=state.manifest_directory,
        metadata=state.metadata,
        preferences=state.preview.preferences,
    )


# ---------------------------------------------------------------------------
# Phase grouping and helpers
# ---------------------------------------------------------------------------


def group_actions_by_phase(
    actions: list[SetupAction],
) -> dict[PluginKind | None, list[SetupAction]]:
    """Group actions into phase buckets keyed by `PluginKind`.

    Post-sync commands (`kind is None`) are stored under the
    `None` key.

    Returns:
        Dict mapping each phase to its action list.
    """
    phases: dict[PluginKind | None, list[SetupAction]] = {k: [] for k in PHASE_ORDER}
    for action in actions:
        phases[action.kind].append(action)
    return phases


def _populate_cli_commands(actions: list[SetupAction], state: ExecutionState) -> None:
    """Set ``cli_command`` on every action from the current plugin state."""
    for action in actions:
        action.cli_command = get_cli_command(
            action,
            state.plugins,
            state.strategy,
        )


async def _propagate_runtime(
    runtime_actions: list[SetupAction],
    plugins: DiscoveredPlugins,
) -> tuple[str, Path] | None:
    """Resolve the interpreter path and propagate to downstream consumers.

    After runtime-provider actions complete, finds the first
    `RuntimeProvider` that can resolve an executable.  The result
    is returned so the caller can store it in its
    :class:`RuntimeContext`.

    Plugin instances are **not** mutated — runtime state is
    threaded explicitly through execution parameters.

    Returns:
        A ``(kind, executable)`` tuple on success, or ``None`` if no
        runtime could be resolved.
    """
    environments = plugins.environments

    # Find a RuntimeProvider among the runtime action installers
    for action in runtime_actions:
        if action.installer is None or action.package is None:
            continue
        env = environments.get(action.installer)
        if env is None or not isinstance(env, RuntimeProvider):
            continue

        kind = cast(type[RuntimeProvider], type(env)).provided_runtime_kind()
        tag = action.package.name
        executable = await env.resolve_executable(tag)
        if executable is None:
            logger.debug(
                'RuntimeProvider %s could not resolve executable for tag %s',
                action.installer,
                tag,
            )
            continue

        logger.info('Runtime resolved: %s -> %s', tag, executable)

        # Make the runtime's directory (and its Scripts/bin sibling)
        # visible on PATH so that downstream tools like pip and python
        # are discoverable via shutil.which() during plugin
        # re-discovery at the next phase transition.
        inject_runtime_path(executable)

        # Use only the first successfully resolved runtime
        return (kind, executable)

    return None


def resolve_deferred_actions(
    actions: list[SetupAction],
    plugins: DiscoveredPlugins,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    preferences: dict[Ecosystem, str] | None = None,
    runtime_context: RuntimeContext | None = None,
) -> None:
    """Resolve deferred actions whose `installer` is `None`.

    After a preceding phase installs new tools (e.g. pip installs pipx),
    plugins are re-discovered and a fresh `BackendResolver` determines
    the correct backend for each deferred action.  Actions that still
    cannot be resolved are left with `installer = None` so that the
    normal execution path reports them as unavailable.

    When a *runtime_context* is provided (i.e. the RUNTIME phase has
    already resolved an interpreter), the resolver uses
    ``RuntimeConsumer.is_available_for(runtime_context)`` to probe
    plugin availability against the target interpreter rather than
    only checking the host process's PATH.

    Args:
        actions: Mutable list of actions to resolve in-place.
        plugins: Freshly-discovered plugin container.
        strategy: Sync strategy (for description verb).
        preferences: Optional ecosystem → plugin-name preferences from the manifest.
        runtime_context: Resolved runtime executables, if any.
    """
    deferred = [a for a in actions if a.installer is None and a.ecosystem is not None]
    if not deferred:
        return

    # Pass runtime_context so that RuntimeConsumer plugins can be
    # probed against the target interpreter, not just the host PATH.
    needed_pairs = {(a.kind, a.ecosystem) for a in deferred if a.kind is not None and a.ecosystem is not None}
    resolver = BackendResolver(
        plugins.all_plugins, preferences, runtime_context=runtime_context, needed_pairs=needed_pairs
    )
    verb = STRATEGY_VERB[strategy]

    for action in deferred:
        assert action.kind is not None
        assert action.ecosystem is not None
        installer = resolver.resolve(action.kind, action.ecosystem)
        if installer is not None:
            action.installer = installer
            action.description = action_description(
                action.kind,
                verb,
                installer,
                package=action.package,
                plugin_target=action.plugin_target,
            )
            logger.info('Deferred action resolved: %s -> %s', action.description, installer)
        else:
            registered = resolver.registered_names(action.kind, action.ecosystem)
            if registered:
                logger.error(
                    'Deferred action permanently unresolved: %s (registered plugins %s for %s/%s are all unavailable)',
                    action.description,
                    registered,
                    action.kind.value,
                    action.ecosystem,
                )
            else:
                logger.error(
                    'Deferred action permanently unresolved: %s (no plugin found for %s/%s)',
                    action.description,
                    action.kind.value,
                    action.ecosystem,
                )


def skip_actions(
    actions: list[SetupAction],
    skip_reason: SkipReason,
    message: str,
    event_queue: asyncio.Queue[ProgressEvent | None],
) -> list[SetupActionResult]:
    """Skip a list of actions, emitting progress events and a warning for each.

    Args:
        actions: The actions to skip.
        skip_reason: Machine-readable skip code.
        message: Human-readable skip detail.
        event_queue: Queue for progress events.

    Returns:
        List of skipped action results.
    """
    results: list[SetupActionResult] = []
    for action in actions:
        logger.warning("Skipping '%s': %s", action.description, message)
        result = SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=skip_reason,
            message=message,
        )
        results.append(result)
        event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.ACTION_COMPLETED,
                action=action,
                result=result,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Project sync execution
# ---------------------------------------------------------------------------


async def _execute_project_sync_actions(
    project_sync_actions: list[SetupAction],
    project_environments: dict[str, ProjectEnvironment] | None,
    manifest_directory: Path,
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
    *,
    runtime_context: RuntimeContext | None = None,
) -> list[SetupActionResult]:
    """Execute PROJECT_SYNC actions sequentially.

    Each action invokes the resolved project-environment plugin's
    `ProjectEnvironment.sync()` method.  When
    `parameters.project_directory` is an explicit `Path` it is
    used as the working directory for every plugin.  Otherwise each
    plugin auto-discovers its project root by walking ancestor
    directories of *manifest_directory* looking for its ecosystem's
    marker file (e.g. `package.json`, `pyproject.toml`).

    Args:
        project_sync_actions: The project sync actions.
        project_environments: Dict of project-environment plugins.
        manifest_directory: Directory containing the manifest file.
        parameters: Setup parameters (dry-run, etc.).
        event_queue: Queue for progress events.
        runtime_context: Resolved runtime paths for this execution run.

    Returns:
        List of action results.
    """
    results: list[SetupActionResult] = []

    for action in project_sync_actions:
        event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        result = await _execute_project_sync(
            action,
            project_environments,
            manifest_directory,
            parameters,
            event_queue=event_queue,
            runtime_context=runtime_context,
        )

        results.append(result)
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.ACTION_COMPLETED,
                action=action,
                result=result,
            )
        )
        if not result.success and parameters.fail_fast:
            logger.error(f'Project sync failed: {action.description} - {result.message}')
            break

    return results


async def _execute_project_sync(
    action: SetupAction,
    project_environments: dict[str, ProjectEnvironment] | None,
    manifest_directory: Path,
    parameters: SetupParameters,
    *,
    event_queue: asyncio.Queue[ProgressEvent | None],
    runtime_context: RuntimeContext | None = None,
) -> SetupActionResult:
    """Execute a single PROJECT_SYNC action.

    The sync command is always run via ``stream_command`` so that
    stdout/stderr lines are emitted as ``SUB_ACTION_PROGRESS``
    events in real time.

    When `parameters.project_directory` is an explicit `Path`
    it is used unconditionally.  Otherwise the plugin's
    `resolve_project_root()` is called to auto-discover the
    project root from *manifest_directory*.  If discovery fails
    (no marker found), *manifest_directory* is used as fallback
    and a warning is logged.

    Args:
        action: The project sync action.
        project_environments: Dict of project-environment plugins.
        manifest_directory: Directory containing the manifest file.
        parameters: Setup parameters.
        event_queue: Queue for streaming progress events.
        runtime_context: Resolved runtime paths for this execution run.

    Returns:
        The result of the sync operation.
    """
    proj_envs = project_environments or {}
    if action.installer is None or action.installer not in proj_envs:
        return SetupActionResult(
            action=action,
            success=False,
            message=f"Project environment '{action.installer}' is not available",
        )

    proj_env = proj_envs[action.installer]

    # Determine the effective directory for this plugin
    effective_dir: Path
    if isinstance(parameters.project_directory, Path):
        # Explicit override — use as-is
        effective_dir = parameters.project_directory
    else:
        # Auto-discover per-plugin project root
        discovered = type(proj_env).resolve_project_root(manifest_directory)
        if discovered is not None:
            effective_dir = discovered
            if discovered != manifest_directory:
                logger.info(
                    "Auto-discovered %s project root for '%s': %s",
                    proj_env.ecosystem(),
                    action.installer,
                    discovered,
                )
        else:
            effective_dir = manifest_directory
            marker = type(proj_env).project_marker()
            if marker is not None:
                logger.warning(
                    "No '%s' found in ancestors of %s; falling back to manifest directory for %s project sync",
                    marker,
                    manifest_directory,
                    proj_env.ecosystem(),
                )

    try:
        # Always stream — build the CLI args from the plugin and
        # run them via stream_command for line-by-line output.
        args = list(proj_env.sync_command(runtime_context=runtime_context))
        if parameters.dry_run:
            args.append('--dry-run')

        def _progress_cb(update: SubActionProgress) -> None:
            event_queue.put_nowait(
                ProgressEvent(
                    kind=ProgressEventKind.SUB_ACTION_PROGRESS,
                    action=action,
                    sub_action=update,
                )
            )

        progress = StreamProgress(
            action=action,
            callback=_progress_cb,
            phase='sync',
        )

        cmd_result = await stream_command(args, progress=progress, cwd=effective_dir, timeout=300.0)
        success = cmd_result.returncode == 0

        if success:
            return SetupActionResult(
                action=action,
                success=True,
                message=f'Synced project via {action.installer}',
            )
        return SetupActionResult(
            action=action,
            success=False,
            message=f'Project sync failed via {action.installer}',
        )
    except Exception as e:
        return SetupActionResult(action=action, success=False, message=str(e))


# ---------------------------------------------------------------------------
# SCM execution
# ---------------------------------------------------------------------------


async def _execute_scm_actions(
    scm_actions: list[SetupAction],
    scm_environments: dict[str, ScmEnvironment] | None,
    working_dir: Path,
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None],
) -> list[SetupActionResult]:
    """Execute SCM_CLONE actions sequentially.

    Each action invokes the resolved SCM-environment plugin's
    `ScmEnvironment.clone()` method.

    Args:
        scm_actions: The SCM clone actions.
        scm_environments: Dict of SCM-environment plugins.
        working_dir: Working directory (manifest location).
        parameters: Setup parameters (dry-run, etc.).
        event_queue: Queue for progress events.

    Returns:
        List of action results.
    """
    results: list[SetupActionResult] = []

    for action in scm_actions:
        event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        result = await _execute_scm_clone(
            action,
            scm_environments,
            working_dir,
            parameters,
            event_queue=event_queue,
        )

        results.append(result)
        event_queue.put_nowait(
            ProgressEvent(
                kind=ProgressEventKind.ACTION_COMPLETED,
                action=action,
                result=result,
            )
        )
        if not result.success and not result.skipped and parameters.fail_fast:
            logger.error(f'SCM clone failed: {action.description} - {result.message}')
            break

    return results


async def _execute_scm_clone(
    action: SetupAction,
    scm_environments: dict[str, ScmEnvironment] | None,
    working_dir: Path,
    parameters: SetupParameters,
    *,
    event_queue: asyncio.Queue[ProgressEvent | None],
) -> SetupActionResult:
    """Execute a single SCM_CLONE action.

    When the tool is ``git``, the clone is run via ``stream_command``
    with ``--progress`` so that stderr progress lines
    (``Receiving objects: 42%``) are emitted as
    ``SUB_ACTION_PROGRESS`` events in real time.  For other SCM
    plugins the plugin's ``clone()`` method is called directly.

    Args:
        action: The SCM clone action.
        scm_environments: Dict of SCM-environment plugins.
        working_dir: Working directory (manifest location).
        parameters: Setup parameters.
        event_queue: Queue for streaming progress events.

    Returns:
        The result of the clone operation.
    """
    scm_envs = scm_environments or {}
    if action.installer is None or action.installer not in scm_envs:
        message = (
            f"No SCM plugin found for installer '{action.installer}'. "
            f'Available SCM plugins: {sorted(scm_envs) or "(none)"}. '
            f'The plugin may not be installed or registered.'
        )
        logger.warning(message)
        return SetupActionResult(
            action=action,
            success=False,
            message=message,
        )

    if action.package is None:
        return SetupActionResult(action=action, success=False, message='No repository URL specified')

    scm_env = scm_envs[action.installer]
    url = action.package.name

    # Clone directly into the working directory, not into a derived subdirectory.
    destination = working_dir

    # Skip if already cloned (checks all remotes and walks up to repo root)
    clone_status = await scm_env.is_cloned(url, destination)

    skip_result = clone_status_to_result(action, clone_status, url, destination)
    if skip_result is not None:
        return skip_result

    # Only MISSING reaches here — the repository needs cloning.
    logger.info("SCM clone needed: repository not found at '%s'", destination)

    if parameters.dry_run:
        return SetupActionResult(action=action, success=True, message=f"Would clone '{url}' into '{destination}'")

    try:
        if scm_env.tool_name() == 'git':
            # Streaming path for git — use --progress to get real-time
            # progress on stderr ("Receiving objects: 42%").
            def _progress_cb(update: SubActionProgress) -> None:
                event_queue.put_nowait(
                    ProgressEvent(
                        kind=ProgressEventKind.SUB_ACTION_PROGRESS,
                        action=action,
                        sub_action=update,
                    )
                )

            progress = StreamProgress(
                action=action,
                callback=_progress_cb,
                phase='cloning',
            )

            cmd_result = await stream_command(
                ['git', 'clone', '--progress', url, str(destination)],
                progress=progress,
                timeout=600.0,
            )
            success = cmd_result.returncode == 0
        else:
            # Non-git SCM plugins — delegate to the plugin's clone()
            success = await scm_env.clone(url, destination, dry=False)

        message = (
            f"Cloned '{url}' via {action.installer}" if success else f"Clone failed for '{url}' via {action.installer}"
        )
        return SetupActionResult(action=action, success=success, message=message)
    except Exception as e:
        return SetupActionResult(action=action, success=False, message=str(e))
