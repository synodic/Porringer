"""Phased execution engine.

Orchestrates the multi-phase setup flow: runtime → packages → tools →
project-sync → SCM → post-sync commands.  Each phase ensures its
prerequisites are met before proceeding.
"""

import asyncio
import logging
import os
import subprocess
import sysconfig
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.plugin_manager import (
    PluginManager,
    find_plugin_manager,
)
from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Ecosystem, Package, PluginKind
from porringer.schema import (
    CloneStatusKind,
    ManifestMetadata,
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SkipReason,
    SubActionProgress,
    SyncStrategy,
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
from .presence import async_dry_run_action, clone_status_to_result
from .resolution import (
    OperationKind,
    ResolutionContext,
    resolve_operation,
    resolved_to_result,
)

logger = logging.getLogger(__name__)


@dataclass
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None
    manifest_directory: Path
    preview: SetupResults
    results: list[SetupActionResult] = field(default_factory=list)
    _resolved_runtime: tuple[str, Path] | None = field(default=None, repr=False)
    """Cached ``(kind, executable)`` pair from the first successful
    runtime resolution.  Set by :meth:`propagate_runtime` and
    re-applied automatically after every plugin re-discovery so that
    newly-created consumer instances inherit the resolved path."""

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
        project environments, and SCM environments, then re-applies
        the cached runtime executable to all new consumer instances.
        """
        refresh_path()
        invalidate_plugin_cache()
        self.plugins = discover_all_plugins().copy()
        self._apply_resolved_runtime()

    def propagate_runtime(self) -> None:
        """Resolve interpreter paths from completed runtime actions and propagate downstream.

        Finds the first ``RuntimeProvider`` among the RUNTIME-phase
        actions, resolves its executable, injects the directory onto
        ``PATH``, and sets ``runtime_executable`` on every matching
        ``RuntimeConsumer``.  The result is cached so that subsequent
        calls to :meth:`refresh_all_plugins` can re-apply it to
        newly-created plugin instances.
        """
        result = _propagate_runtime(
            self.phases[PluginKind.RUNTIME],
            self.plugins,
        )
        if result is not None:
            self._resolved_runtime = result

    def _apply_resolved_runtime(self) -> None:
        """Re-apply the cached runtime executable to all current consumers.

        No-op when no runtime has been resolved yet.
        """
        if self._resolved_runtime is None:
            return
        kind, executable = self._resolved_runtime
        all_plugins = self.plugins.all_plugins
        for name, plugin in all_plugins.items():
            if isinstance(plugin, RuntimeConsumer):
                consumer_type = cast(type[RuntimeConsumer], type(plugin))
                if consumer_type.consumed_runtime_kind() == kind:
                    plugin.runtime_executable = executable
                    logger.debug('Re-applied runtime_executable on %s to %s', name, executable)

    # -- result helpers ------------------------------------------------

    def emit(self, event: ProgressEvent) -> None:
        """Put *event* on the event queue if one is attached."""
        if self.event_queue is not None:
            self.event_queue.put_nowait(event)

    @property
    def plugin_context(self) -> PluginContext:
        """Plugin-management context for this execution run."""
        return PluginContext(project_environments=self.project_environments)

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
            self.plugin_context,
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


@dataclass
class PluginContext:
    """Bundled plugin-management context for the execution helpers.

    Groups the project-path and project-environment references that
    flow through ``execute_package_actions`` → ``execute_package``.
    """

    project_path: Path | None = None
    project_environments: dict[str, ProjectEnvironment] | None = None


# ---------------------------------------------------------------------------
# PATH refresh
# ---------------------------------------------------------------------------

_path_lock = threading.Lock()


def _prepend_to_path(dirs: list[str], *, require_exists: bool = False) -> None:
    """Prepend directories to ``os.environ['PATH']`` if not already present.

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

    After packages are installed, executables such as ``pipx`` may
    have been placed in directories not yet on the running process's
    ``PATH`` (e.g. ``~/.local/bin`` on Unix, or ``Scripts/`` on
    Windows).  This function detects those directories and prepends
    them so that subsequent ``shutil.which()`` calls succeed.
    """
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
) -> SetupActionResult:
    """Execute a post-install command with real-time output streaming.

    Uses ``asyncio.create_subprocess_exec`` so the event loop is never
    blocked, and streams stdout/stderr line-by-line as
    ``SUB_ACTION_PROGRESS`` events when an *event_queue* is provided.

    Args:
        action: The command action.
        working_dir: Working directory for the command.
        timeout: Timeout in seconds.
        event_queue: Optional queue to emit sub-action progress into.

    Returns:
        The result of the command execution.
    """
    if action.command is None or len(action.command) == 0:
        return SetupActionResult(action=action, success=False, message='No command specified')

    logger.info(f'Running command: {" ".join(action.command)}')

    if event_queue is not None:
        # Streaming path — line-by-line output via stream_command
        _eq = event_queue  # bind for closure type-narrowing

        def _progress_cb(update: SubActionProgress) -> None:
            _eq.put_nowait(
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
    else:
        # Non-streaming path — run in executor to avoid blocking the loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_command_sync, action, working_dir, timeout)


def _run_command_sync(action: SetupAction, working_dir: Path, timeout: int) -> SetupActionResult:
    """Synchronous subprocess helper for post-sync commands.

    Runs the command with ``subprocess.run`` and returns a result.
    Called via ``run_in_executor`` so the event loop stays unblocked.
    """
    assert action.command is not None  # guaranteed by caller guard

    try:
        result = subprocess.run(
            action.command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        if result.returncode == 0:
            return SetupActionResult(action=action, success=True)
        else:
            stderr = result.stderr.strip() if result.stderr else 'Unknown error'
            return SetupActionResult(
                action=action,
                success=False,
                message=f'Exit code {result.returncode}: {stderr}',
            )
    except subprocess.TimeoutExpired:
        message = f'Command timed out after {timeout} seconds'
        logger.error(message)
        return SetupActionResult(action=action, success=False, message=message)
    except FileNotFoundError:
        message = f'Command not found: {action.command[0]}'
        return SetupActionResult(action=action, success=False, message=message)
    except Exception as e:
        return SetupActionResult(action=action, success=False, message=str(e))


# ---------------------------------------------------------------------------
# Package execution helpers
# ---------------------------------------------------------------------------


async def execute_package(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    plugin_context: PluginContext | None = None,
) -> SetupActionResult:
    """Execute a package install or upgrade based on the strategy.

    Delegates to :func:`resolve_operation` to determine the correct
    operation (install, upgrade, or skip), then dispatches to the
    appropriate execution helper.

    Args:
        action: The package action.
        environments: Dict of instantiated environment plugins.
        strategy: The sync strategy.
        event_queue: Optional queue to emit sub-action events into.
        plugin_context: Optional plugin-management context providing
            project-path and project-environment references.

    Returns:
        The result of the operation.
    """
    if action.installer is None or action.package is None:
        return SetupActionResult(action=action, success=False, message='Installer or package not specified')

    project_path = plugin_context.project_path if plugin_context else None
    project_environments = plugin_context.project_environments if plugin_context else None

    resolved = await resolve_operation(
        action,
        environments,
        strategy,
        ResolutionContext(
            project_path=project_path,
            project_environments=project_environments,
        ),
    )

    # --- Skip -------------------------------------------------------------
    if resolved.operation == OperationKind.SKIP:
        logger.info("Skipping '%s': %s", action.package, resolved.message)
        return resolved_to_result(resolved)

    # --- Plugin-management actions ----------------------------------------
    if action.plugin_target is not None:
        is_install = resolved.operation == OperationKind.INSTALL
        return await _attempt_plugin_operation(
            action,
            is_install=is_install,
            event_queue=event_queue,
            plugin_manager=resolved.plugin_manager,
            project_environments=project_environments,
        )

    # --- Normal package actions -------------------------------------------
    if action.installer not in environments:
        msg = f"Installer '{action.installer}' is not available"
        return SetupActionResult(action=action, success=False, message=msg)

    environment = environments[action.installer]
    effective = SyncStrategy.MINIMAL if resolved.operation == OperationKind.INSTALL else strategy
    verb = 'Installing' if resolved.operation == OperationKind.INSTALL else 'Upgrading'
    logger.info(f"{verb} '{action.package}' via {action.installer}")
    return await _attempt_package_operation(action, environment, effective, event_queue)


async def _attempt_package_operation(
    action: SetupAction,
    environment: Environment,
    strategy: SyncStrategy,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
) -> SetupActionResult:
    """Attempt to install or upgrade a package via the given environment plugin.

    Args:
        action: The package action.
        environment: The environment plugin to use.
        strategy: Whether to install or upgrade.
        event_queue: Optional queue to emit sub-action events into.

    Returns:
        The result of the attempt.
    """
    is_install = strategy == SyncStrategy.MINIMAL
    if is_install:
        execute: Callable[[PackageParameters], Awaitable[Package | None]] = environment.async_install
        verb, verb_past = 'install', 'Installed'
    else:
        execute = environment.async_upgrade
        verb, verb_past = 'upgrade', 'Upgraded'

    return await _attempt_operation(
        action,
        spec=OperationSpec(
            execute=execute,
            verb=verb,
            verb_past=verb_past,
        ),
        event_queue=event_queue,
    )


async def _attempt_plugin_operation(
    action: SetupAction,
    *,
    is_install: bool,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    plugin_manager: PluginManager | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> SetupActionResult:
    """Add or update a plugin via its native ``PluginManager``.

    Uses the *plugin_manager* resolved during operation resolution
    when available, falling back to a fresh lookup when not provided.

    Args:
        action: The plugin action (``plugin_target`` must be set).
        is_install: ``True`` to add (install) the plugin,
            ``False`` to update (upgrade) it.
        event_queue: Optional queue to emit sub-action events into.
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

    if is_install:
        execute = plugin_manager.async_plugin_add
        verb, verb_past = 'add plugin', 'Added'
    else:
        execute = plugin_manager.async_plugin_update
        verb, verb_past = 'update plugin', 'Updated'

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
            success_suffix=f' to {action.plugin_target.name} (native)',
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
) -> SetupActionResult:
    """Core helper that runs an async package operation with standard error handling.

    Builds the progress callback, constructs `PackageParameters`,
    calls *spec.execute*, and catches the standard exception set.

    Args:
        action: The action being executed.
        spec: The operation specification (callable + verb forms).
        event_queue: Optional queue to emit sub-action events into.

    Returns:
        The result of the attempt.
    """
    success = False
    message = ''

    sub_action_cb = None
    if event_queue is not None:
        eq = event_queue

        def sub_action_cb(update: SubActionProgress) -> None:
            eq.put_nowait(
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
    plugin_context: PluginContext | None = None,
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
                plugin_context=plugin_context,
                parameters=parameters,
            ),
            True,
        )

    parallel_actions, sequential_actions = _group_actions_by_parallelism(package_actions, environments)

    results: list[SetupActionResult] = []

    # Execute parallel actions concurrently
    if parallel_actions:
        parallel_results, should_continue = await _run_parallel_packages(
            parallel_actions,
            environments,
            parameters,
            event_queue,
            plugin_context,
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
        plugin_context,
    )
    results.extend(sequential_results)

    return results, should_continue


async def _dry_run_package_actions(
    package_actions: list[SetupAction],
    environments: dict[str, Environment],
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
    *,
    plugin_context: PluginContext | None = None,
    parameters: SetupParameters | None = None,
) -> list[SetupActionResult]:
    """Execute dry-run for package actions in parallel.

    All actions are dispatched concurrently via ``asyncio.TaskGroup``.
    Results are emitted in the original action order regardless of
    which checks finish first, preserving deterministic card ordering
    for GUI consumers.
    """
    project_path = plugin_context.project_path if plugin_context else None
    project_environments = plugin_context.project_environments if plugin_context else None

    result_slots: list[SetupActionResult | None] = [None] * len(package_actions)

    async def _check(index: int, action: SetupAction) -> None:
        # Emit ACTION_STARTED *before* the check so GUI clients can
        # show a spinner while the dry-run is in progress.
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
        try:
            result = await async_dry_run_action(
                action,
                environments,
                project_path=project_path,
                project_environments=project_environments,
                parameters=parameters,
            )
        except Exception as exc:
            logger.debug('Dry-run check failed for %s: %s', action.description, exc)
            result = SetupActionResult(action=action, success=False, message=str(exc))
        result_slots[index] = result
        if event_queue is not None:
            event_queue.put_nowait(
                ProgressEvent(
                    kind=ProgressEventKind.ACTION_COMPLETED,
                    action=action,
                    result=result,
                )
            )

    async with asyncio.TaskGroup() as tg:
        for i, action in enumerate(package_actions):
            tg.create_task(_check(i, action))

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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
    plugin_context: PluginContext | None = None,
) -> tuple[list[SetupActionResult], bool]:
    """Run package actions sequentially."""
    results: list[SetupActionResult] = []
    for action in sequential_actions:
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
        result = await execute_package(
            action,
            environments,
            parameters.strategy,
            event_queue,
            plugin_context,
        )
        results.append(result)
        if event_queue is not None:
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
    plugin_context: PluginContext | None = None,
) -> tuple[list[SetupActionResult], bool]:
    """Run package actions in parallel using TaskGroup.

    Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.
    All tasks are automatically cancelled if any raises an unhandled exception.

    Returns:
        Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
    """
    results: dict[int, SetupActionResult] = {}
    action_indices = {id(action): i for i, action in enumerate(parallel_actions)}

    async def package_with_event(action: SetupAction) -> None:
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
        try:
            result = await execute_package(
                action,
                environments,
                parameters.strategy,
                event_queue,
                plugin_context,
            )
        except Exception as e:
            result = SetupActionResult(action=action, success=False, message=str(e))
        if event_queue is not None:
            event_queue.put_nowait(
                ProgressEvent(
                    kind=ProgressEventKind.ACTION_COMPLETED,
                    action=action,
                    result=result,
                )
            )
        results[action_indices[id(action)]] = result

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
        if state.event_queue is not None:
            state.event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        if state.parameters.dry_run:
            result = await async_dry_run_action(
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
        if state.event_queue is not None:
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


def _plugins_discovered_event(plugins: DiscoveredPlugins) -> ProgressEvent:
    """Build a ``PLUGINS_DISCOVERED`` progress event.

    Collects ``is_available()`` for every discovered plugin and
    returns a single ``ProgressEvent`` that GUI clients can use to
    render availability badges.
    """
    plugin_availability: dict[str, bool] = {}
    for name, env in plugins.environments.items():
        plugin_availability[name] = env.is_available()
    for name, proj in plugins.project_environments.items():
        plugin_availability[name] = proj.is_available()
    for name, scm in plugins.scm_environments.items():
        plugin_availability[name] = scm.is_available()
    logger.info('Plugins discovered — availability: %s', plugin_availability)
    return ProgressEvent(
        kind=ProgressEventKind.PLUGINS_DISCOVERED,
        plugin_names=sorted(plugin_availability.keys()),
        plugin_availability=plugin_availability,
    )


async def execute_single(
    preview: SetupResults,
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
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
        event_queue: Optional queue to emit ``ProgressEvent`` items into.
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
    if plugins_discovered_here and event_queue is not None:
        event_queue.put_nowait(_plugins_discovered_event(plugins))

    state = ExecutionState(
        actions=actions,
        phases=group_actions_by_phase(actions),
        # Shallow-copy the plugin dicts so that mutations (e.g.
        # runtime_executable propagation) don't leak back into
        # the caller's shared DiscoveredPlugins object.
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


def _propagate_runtime(
    runtime_actions: list[SetupAction],
    plugins: DiscoveredPlugins,
) -> tuple[str, Path] | None:
    """Resolve the interpreter path and propagate to downstream consumers.

    After runtime-provider actions complete, finds the first
    `RuntimeProvider` that can resolve an executable and sets
    `runtime_executable` on all `RuntimeConsumer` plugins
    whose `consumed_runtime_kind` matches the provider's
    `provided_runtime_kind`.

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
        executable = env.resolve_executable(tag)
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

        # Propagate to all plugins (environment + project-environment) that consume this runtime kind
        all_plugins = plugins.all_plugins
        for name, downstream in all_plugins.items():
            if isinstance(downstream, RuntimeConsumer):
                downstream_type = cast(type[RuntimeConsumer], type(downstream))
                if downstream_type.consumed_runtime_kind() == kind:
                    downstream.runtime_executable = executable
                    logger.debug('Set runtime_executable on %s to %s', name, executable)

        # Use only the first successfully resolved runtime
        return (kind, executable)

    return None


def resolve_deferred_actions(
    actions: list[SetupAction],
    plugins: DiscoveredPlugins,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    preferences: dict[Ecosystem, str] | None = None,
) -> None:
    """Resolve deferred actions whose `installer` is `None`.

    After a preceding phase installs new tools (e.g. pip installs pipx),
    plugins are re-discovered and a fresh `BackendResolver` determines
    the correct backend for each deferred action.  Actions that still
    cannot be resolved are left with `installer = None` so that the
    normal execution path reports them as unavailable.

    Args:
        actions: Mutable list of actions to resolve in-place.
        plugins: Freshly-discovered plugin container.
        strategy: Sync strategy (for description verb).
        preferences: Optional ecosystem → plugin-name preferences from the manifest.
    """
    deferred = [a for a in actions if a.installer is None and a.ecosystem is not None]
    if not deferred:
        return

    resolver = BackendResolver(plugins.all_plugins, preferences)
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
) -> list[SetupActionResult]:
    """Skip a list of actions, emitting progress events and a warning for each.

    Args:
        actions: The actions to skip.
        skip_reason: Machine-readable skip code.
        message: Human-readable skip detail.
        event_queue: Optional queue for progress events.

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
        if event_queue is not None:
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
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
        event_queue: Optional queue for progress events.

    Returns:
        List of action results.
    """
    results: list[SetupActionResult] = []

    for action in project_sync_actions:
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        result = await _execute_project_sync(
            action,
            project_environments,
            manifest_directory,
            parameters,
            event_queue=event_queue,
        )

        results.append(result)
        if event_queue is not None:
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
) -> SetupActionResult:
    """Execute a single PROJECT_SYNC action.

    When an *event_queue* is provided the sync command is run via
    ``stream_command`` so that stdout/stderr lines are emitted as
    ``SUB_ACTION_PROGRESS`` events in real time.  Otherwise the
    plugin's synchronous ``sync()`` method is called via
    ``run_in_executor``.

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
        event_queue: Optional queue for streaming progress events.

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

    params = ProjectSyncParameters(directory=effective_dir, dry=parameters.dry_run)

    try:
        if event_queue is not None:
            # Streaming path — build the CLI args from the plugin and
            # run them via stream_command for line-by-line output.
            args = list(proj_env.sync_command())
            if params.dry:
                args.append('--dry-run')

            _eq = event_queue  # bind for closure type-narrowing

            def _progress_cb(update: SubActionProgress) -> None:
                _eq.put_nowait(
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

            cmd_result = await stream_command(args, progress=progress, timeout=300.0)
            success = cmd_result.returncode == 0
        else:
            # Non-streaming — delegate to the plugin's synchronous sync()
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, proj_env.sync, params)

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
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
) -> list[SetupActionResult]:
    """Execute SCM_CLONE actions sequentially.

    Each action invokes the resolved SCM-environment plugin's
    `ScmEnvironment.clone()` method.

    Args:
        scm_actions: The SCM clone actions.
        scm_environments: Dict of SCM-environment plugins.
        working_dir: Working directory (manifest location).
        parameters: Setup parameters (dry-run, etc.).
        event_queue: Optional queue for progress events.

    Returns:
        List of action results.
    """
    results: list[SetupActionResult] = []

    for action in scm_actions:
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

        result = await _execute_scm_clone(
            action,
            scm_environments,
            working_dir,
            parameters,
            event_queue=event_queue,
        )

        results.append(result)
        if event_queue is not None:
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
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
) -> SetupActionResult:
    """Execute a single SCM_CLONE action.

    When an *event_queue* is provided and the tool is ``git``, the
    clone is run via ``stream_command`` with ``--progress`` so that
    stderr progress lines (``Receiving objects: 42%``) are emitted
    as ``SUB_ACTION_PROGRESS`` events in real time.  Otherwise the
    plugin's synchronous ``clone()`` is called via ``run_in_executor``.

    Args:
        action: The SCM clone action.
        scm_environments: Dict of SCM-environment plugins.
        working_dir: Working directory (manifest location).
        parameters: Setup parameters.
        event_queue: Optional queue for streaming progress events.

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
    clone_status = scm_env.is_cloned(url, destination)

    skip_result = clone_status_to_result(action, clone_status, url, destination)
    if skip_result is not None:
        return skip_result

    # Only MISSING reaches here — the repository needs cloning.
    logger.info("SCM clone needed: repository not found at '%s'", destination)

    if parameters.dry_run:
        return SetupActionResult(
            action=action, success=True, message=f"Would clone '{url}' into '{destination}'"
        )

    try:
        if event_queue is not None and scm_env.tool_name() == 'git':
            # Streaming path for git — use --progress to get real-time
            # progress on stderr ("Receiving objects: 42%").
            _eq = event_queue  # bind for closure type-narrowing

            def _progress_cb(update: SubActionProgress) -> None:
                _eq.put_nowait(
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
            # Non-streaming fallback
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, lambda: scm_env.clone(url, destination, dry=False))

        message = (
            f"Cloned '{url}' via {action.installer}" if success else f"Clone failed for '{url}' via {action.installer}"
        )
        return SetupActionResult(action=action, success=success, message=message)
    except Exception as e:
        return SetupActionResult(action=action, success=False, message=str(e))
