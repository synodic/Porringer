"""Phased execution engine.

Orchestrates the multi-phase setup flow: runtime → packages → tools →
project-sync → SCM → post-sync commands.  Each phase ensures its
prerequisites are met before proceeding.
"""

from __future__ import annotations

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
from porringer.core.plugin_schema.plugin_manager import find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment, ProjectSyncParameters
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Package, PluginKind
from porringer.schema import (
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

from .action_builder import PHASE_ORDER, STRATEGY_VERB, action_description, get_cli_command
from .discovery import discover_all_plugins, discover_plugins
from .presence import dry_run_action, is_package_installed

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    """Mutable state for a single phased execution run.

    Owns the discovered plugin dicts, grouped phase actions, and
    common parameters that every phase needs.  Provides
    ``phase_transition()`` and ``propagate_runtime()`` as methods
    so callers don't need to pass a half-dozen arguments.
    """

    actions: list[SetupAction]
    phases: dict[PluginKind | None, list[SetupAction]]
    environments: dict[str, Environment]
    project_environments: dict[str, ProjectEnvironment] | None
    scm_environments: dict[str, ScmEnvironment] | None
    parameters: SetupParameters
    event_queue: asyncio.Queue[ProgressEvent | None] | None
    manifest_directory: Path
    fallback_dir: Path
    skip_project: bool
    manifest_path: Path | None = None
    metadata: ManifestMetadata | None = None
    results: list[SetupActionResult] = field(default_factory=list)
    _resolved_runtime: tuple[str, Path] | None = field(default=None, repr=False)
    """Cached ``(kind, executable)`` pair from the first successful
    runtime resolution.  Set by :meth:`propagate_runtime` and
    re-applied automatically after every plugin re-discovery so that
    newly-created consumer instances inherit the resolved path."""

    # -- convenience properties ----------------------------------------

    @property
    def strategy(self) -> SyncStrategy:
        """The sync strategy from the current parameters."""
        return self.parameters.strategy

    # -- phase-transition machinery ------------------------------------

    def phase_transition(self) -> None:
        """Refresh PATH, re-discover environment plugins, and resolve deferred actions.

        Mutates ``self.environments`` in place and updates
        descriptions / CLI commands on any newly-resolved actions.
        The cached runtime executable (if any) is automatically
        re-propagated to newly-created consumer instances.
        """
        refresh_path()
        self.environments = discover_plugins('environment', Environment, check_dependencies=True)
        self._apply_resolved_runtime()

        for phase_actions in self.phases.values():
            if phase_actions:
                _resolve_deferred_actions(phase_actions, self.environments, self.strategy)

        for phase_actions in self.phases.values():
            for action in phase_actions:
                if action.cli_command is None or action.installer is not None:
                    action.cli_command = get_cli_command(
                        action,
                        self.environments,
                        self.strategy,
                        self.project_environments,
                        self.scm_environments,
                    )

    def propagate_runtime(self) -> None:
        """Resolve interpreter paths from completed runtime actions and propagate downstream.

        Finds the first ``RuntimeProvider`` among the RUNTIME-phase
        actions, resolves its executable, injects the directory onto
        ``PATH``, and sets ``runtime_executable`` on every matching
        ``RuntimeConsumer``.  The result is cached so that subsequent
        calls to :meth:`phase_transition` or
        :meth:`refresh_project_environments` can re-apply it to
        newly-created plugin instances.
        """
        result = _propagate_runtime(
            self.phases[PluginKind.RUNTIME],
            self.environments,
            self.project_environments,
        )
        if result is not None:
            self._resolved_runtime = result

    def refresh_project_environments(self) -> None:
        """Re-discover project-environment plugins and re-propagate the runtime.

        Replaces ``self.project_environments`` with freshly-discovered
        instances and re-applies the cached runtime executable so that
        new ``RuntimeConsumer`` project environments inherit the
        resolved interpreter path.
        """
        self.project_environments = discover_plugins('project_environment', ProjectEnvironment)
        self._apply_resolved_runtime()

    def _apply_resolved_runtime(self) -> None:
        """Re-apply the cached runtime executable to all current consumers.

        No-op when no runtime has been resolved yet.
        """
        if self._resolved_runtime is None:
            return
        kind, executable = self._resolved_runtime
        proj_envs: dict[str, ProjectEnvironment] = self.project_environments or {}
        all_plugins: dict[str, Environment | ProjectEnvironment] = {**self.environments, **proj_envs}
        for name, plugin in all_plugins.items():
            if isinstance(plugin, RuntimeConsumer):
                consumer_type = cast(type[RuntimeConsumer], type(plugin))
                if consumer_type.consumed_runtime_kind() == kind:
                    plugin.runtime_executable = executable
                    logger.debug('Re-applied runtime_executable on %s to %s', name, executable)

    # -- result helpers ------------------------------------------------

    @property
    def plugin_context(self) -> PluginContext:
        """Plugin-management context for this execution run."""
        return PluginContext(project_environments=self.project_environments)

    def early_return(self) -> SetupResults:
        """Create a ``SetupResults`` from the results accumulated so far."""
        return SetupResults(
            actions=self.actions,
            results=self.results,
            manifest_path=self.manifest_path,
            root_directory=self.manifest_directory,
            metadata=self.metadata,
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


def execute_run_command(action: SetupAction, working_dir: Path, timeout: int) -> SetupActionResult:
    """Executes a post-install command.

    Args:
        action: The command action.
        working_dir: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        The result of the command execution.
    """
    if action.command is None or len(action.command) == 0:
        return SetupActionResult(action=action, success=False, message='No command specified')

    logger.info(f'Running command: {" ".join(action.command)}')

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
            return SetupActionResult(action=action, success=False, message=f'Exit code {result.returncode}: {stderr}')
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

    In MINIMAL strategy, skips already-installed packages.
    In LATEST/EXACT strategy, upgrades installed packages and falls back to
    install for packages that are not yet present.

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

    # --- Plugin-management actions -----------------------------------------
    if action.plugin_target is not None:
        project_environments = plugin_context.project_environments if plugin_context else None
        manager = find_plugin_manager(action.plugin_target.name, project_environments)

        # Check presence before adding
        if manager is not None and strategy == SyncStrategy.MINIMAL:
            try:
                installed = manager.installed_plugins()
                is_plugin_installed, detail = is_package_installed(action.package, installed)
                if is_plugin_installed:
                    logger.info("Skipping plugin '%s': %s", action.package, detail)
                    return SetupActionResult(
                        action=action,
                        success=True,
                        skipped=True,
                        skip_reason=SkipReason.ALREADY_INSTALLED,
                        message=detail,
                    )
            except Exception as e:
                logger.debug('Could not check installed plugins for %s: %s', action.plugin_target.name, e)

        return await _attempt_plugin_add(action, event_queue, project_environments=project_environments)

    if action.installer not in environments:
        msg = f"Installer '{action.installer}' is not available"
        return SetupActionResult(action=action, success=False, message=msg)

    environment = environments[action.installer]

    # --- Normal install / upgrade -----------------------------------------
    # Check if package is already installed
    is_installed = False
    installed_detail: str | None = None
    validator = type(environment).package_name_validator()
    project_path = plugin_context.project_path if plugin_context else None
    try:
        loop = asyncio.get_running_loop()
        installed_packages = await loop.run_in_executor(None, lambda: environment.packages(project_path=project_path))
        is_installed, installed_detail = is_package_installed(
            action.package, installed_packages, validator, action.kind
        )
    except PluginError as e:
        logger.debug(f'Plugin error checking packages for {action.installer}: {e}')
    except Exception as e:
        logger.debug(f'Could not check installed packages for {action.installer}: {e}')

    if strategy == SyncStrategy.MINIMAL and is_installed:
        logger.info(f"Skipping '{action.package}': {installed_detail}")
        return SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=SkipReason.ALREADY_INSTALLED,
            message=installed_detail,
        )

    # Install if not present, otherwise honour the requested strategy
    effective = SyncStrategy.MINIMAL if not is_installed else strategy
    verb = 'Installing' if effective == SyncStrategy.MINIMAL else 'Upgrading'
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


async def _attempt_plugin_add(
    action: SetupAction,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    *,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> SetupActionResult:
    """Add a plugin to a parent tool via its native ``PluginManager``.

    Looks up a ``PluginManager`` for the target tool among the
    *project_environments*.  If none is found (or the tool is not on
    PATH), the action fails.

    Args:
        action: The plugin action (``plugin_target`` must be set).
        event_queue: Optional queue to emit sub-action events into.
        project_environments: Dict of project-environment plugins,
            checked for ``PluginManager`` implementations.

    Returns:
        The result of the attempt.
    """
    assert action.plugin_target is not None
    assert action.package is not None

    plugin_manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if plugin_manager is None:
        msg = f"No PluginManager found for '{action.plugin_target.name}'"
        return SetupActionResult(action=action, success=False, message=msg)

    logger.info(
        "Using native plugin management for '%s' via %s",
        action.plugin_target.name,
        type(plugin_manager).__name__,
    )
    return await _attempt_operation(
        action,
        spec=OperationSpec(
            execute=plugin_manager.async_plugin_add,
            verb='add plugin',
            verb_past='Added',
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
            eq.put_nowait(ProgressEvent(kind=ProgressEventKind.SUB_ACTION_PROGRESS, action=action, sub_action=update))

    try:
        if action.package is None:
            return SetupActionResult(action=action, success=False, message='No package specified')
        params = PackageParameters(
            package=action.package,
            dry=False,
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
            _dry_run_package_actions(
                package_actions,
                environments,
                parameters.strategy,
                event_queue,
                plugin_context=plugin_context,
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


def _dry_run_package_actions(
    package_actions: list[SetupAction],
    environments: dict[str, Environment],
    strategy: SyncStrategy,
    event_queue: asyncio.Queue[ProgressEvent | None] | None,
    *,
    plugin_context: PluginContext | None = None,
) -> list[SetupActionResult]:
    """Execute dry-run for package actions."""
    project_path = plugin_context.project_path if plugin_context else None
    project_environments = plugin_context.project_environments if plugin_context else None
    results: list[SetupActionResult] = []
    for action in package_actions:
        result = dry_run_action(
            action,
            environments,
            strategy,
            project_path=project_path,
            project_environments=project_environments,
        )
        results.append(result)
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
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
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
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
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
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
        if state.parameters.dry_run:
            result = dry_run_action(
                action,
                state.environments,
                state.strategy,
            )
        else:
            result = execute_run_command(action, state.fallback_dir, state.parameters.timeout)
        results.append(result)
        if state.event_queue is not None:
            state.event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            state.event_queue.put_nowait(
                ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
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


async def execute_single(
    preview: SetupResults,
    parameters: SetupParameters,
    event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
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

    Returns:
        SetupResults containing the results of each action.
    """
    actions = preview.actions
    assert preview.root_directory is not None  # guaranteed by parse_manifest
    root_directory = preview.root_directory

    logger.info(f'Executing {len(actions)} setup actions async (dry_run={parameters.dry_run})')

    plugins = discover_all_plugins()

    state = ExecutionState(
        actions=actions,
        phases=group_actions_by_phase(actions),
        environments=plugins.environments,
        project_environments=plugins.project_environments,
        scm_environments=plugins.scm_environments,
        parameters=parameters,
        event_queue=event_queue,
        manifest_directory=root_directory,
        fallback_dir=determine_fallback_dir(parameters, root_directory),
        skip_project=parameters.project_directory is False,
        manifest_path=preview.manifest_path,
        metadata=preview.metadata,
    )

    # Populate CLI commands for all resolved actions
    for action in actions:
        action.cli_command = get_cli_command(
            action,
            state.environments,
            state.strategy,
            state.project_environments,
            state.scm_environments,
        )

    # --- Phase 1: runtime-provider actions (pim / pyenv) ---------------
    if state.phases[PluginKind.RUNTIME]:
        runtime_results, should_continue = await execute_package_actions(
            state.phases[PluginKind.RUNTIME],
            state.environments,
            state.parameters,
            state.event_queue,
            state.plugin_context,
        )
        state.results.extend(runtime_results)
        if not should_continue:
            return state.early_return()
        state.propagate_runtime()

        # Phase transition: re-discover plugins now that the runtime
        # is installed and its directories are on PATH, so that
        # downstream consumers (pip, uv, etc.) become available.
        state.phase_transition()

    # --- Phase 2a: package-kind actions (pip, uv, etc.) ---------------
    if state.phases[PluginKind.PACKAGE]:
        package_results, should_continue = await execute_package_actions(
            state.phases[PluginKind.PACKAGE],
            state.environments,
            state.parameters,
            state.event_queue,
            state.plugin_context,
        )
        state.results.extend(package_results)
        if not should_continue:
            return state.early_return()

    # --- Phase 2b: tool-kind actions (pipx, etc.) ---------------------
    if state.phases[PluginKind.TOOL]:
        # Phase transition: re-discover plugins so that tools installed
        # in Phase 2a (e.g. pipx via pip) are now available as backends.
        state.phase_transition()

        tool_results, should_continue = await execute_package_actions(
            state.phases[PluginKind.TOOL],
            state.environments,
            state.parameters,
            state.event_queue,
            state.plugin_context,
        )
        state.results.extend(tool_results)
        if not should_continue:
            return state.early_return()

    # --- Phase 3: project sync ----------------------------------------
    if state.phases[PluginKind.PROJECT]:
        # Re-discover project environments in case tools installed in
        # earlier phases provide new project-environment backends.
        state.refresh_project_environments()

        state.results.extend(await handle_project_phase(state.phases[PluginKind.PROJECT], state))

    # --- Phase 4: SCM clone -------------------------------------------
    if state.phases[PluginKind.SCM]:
        state.results.extend(
            await _execute_scm_actions(
                state.phases[PluginKind.SCM],
                state.scm_environments,
                state.fallback_dir,
                state.parameters,
                state.event_queue,
            )
        )

    # --- Phase 5: post-sync commands ----------------------------------
    if state.phases[None]:
        state.results.extend(await execute_command_actions(state.phases[None], state))

    return SetupResults(
        actions=actions,
        results=state.results,
        manifest_path=state.manifest_path,
        root_directory=state.manifest_directory,
        metadata=state.metadata,
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


def _propagate_runtime(
    runtime_actions: list[SetupAction],
    environments: dict[str, Environment],
    project_environments: dict[str, ProjectEnvironment] | None = None,
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
    proj_envs = project_environments or {}

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
            logger.debug('RuntimeProvider %s could not resolve executable for tag %s', action.installer, tag)
            continue

        logger.info('Runtime resolved: %s -> %s', tag, executable)

        # Make the runtime's directory (and its Scripts/bin sibling)
        # visible on PATH so that downstream tools like pip and python
        # are discoverable via shutil.which() during plugin
        # re-discovery at the next phase transition.
        inject_runtime_path(executable)

        # Propagate to all plugins (environment + project-environment) that consume this runtime kind
        all_plugins: dict[str, Environment | ProjectEnvironment] = {**environments, **proj_envs}
        for name, downstream in all_plugins.items():
            if isinstance(downstream, RuntimeConsumer):
                downstream_type = cast(type[RuntimeConsumer], type(downstream))
                if downstream_type.consumed_runtime_kind() == kind:
                    downstream.runtime_executable = executable
                    logger.debug('Set runtime_executable on %s to %s', name, executable)

        # Use only the first successfully resolved runtime
        return (kind, executable)

    return None


def _resolve_deferred_actions(
    actions: list[SetupAction],
    environments: dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
) -> None:
    """Resolve deferred actions whose `installer` is `None`.

    After a preceding phase installs new tools (e.g. pip installs pipx),
    plugins are re-discovered and a fresh `BackendResolver` determines
    the correct backend for each deferred action.  Actions that still
    cannot be resolved are left with `installer = None` so that the
    normal execution path reports them as unavailable.

    Args:
        actions: Mutable list of actions to resolve in-place.
        environments: Freshly-discovered environment plugins.
        strategy: Sync strategy (for description verb).
    """
    deferred = [a for a in actions if a.installer is None and a.ecosystem is not None]
    if not deferred:
        return

    resolver = BackendResolver(environments)
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
            logger.warning('Deferred action still unresolved: %s', action.description)


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
        result = SetupActionResult(action=action, success=True, skipped=True, skip_reason=skip_reason, message=message)
        results.append(result)
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
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

        result = await _execute_project_sync(action, project_environments, manifest_directory, parameters)

        results.append(result)
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
        if not result.success and parameters.fail_fast:
            logger.error(f'Project sync failed: {action.description} - {result.message}')
            break

    return results


async def _execute_project_sync(
    action: SetupAction,
    project_environments: dict[str, ProjectEnvironment] | None,
    manifest_directory: Path,
    parameters: SetupParameters,
) -> SetupActionResult:
    """Execute a single PROJECT_SYNC action.

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

    Returns:
        The result of the sync operation.
    """
    proj_envs = project_environments or {}
    if action.installer is None or action.installer not in proj_envs:
        return SetupActionResult(
            action=action, success=False, message=f"Project environment '{action.installer}' is not available"
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
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, proj_env.sync, params)
        if success:
            return SetupActionResult(action=action, success=True, message=f'Synced project via {action.installer}')
        return SetupActionResult(action=action, success=False, message=f'Project sync failed via {action.installer}')
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

        result = await _execute_scm_clone(action, scm_environments, working_dir, parameters)

        results.append(result)
        if event_queue is not None:
            event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result))
        if not result.success and not result.skipped and parameters.fail_fast:
            logger.error(f'SCM clone failed: {action.description} - {result.message}')
            break

    return results


async def _execute_scm_clone(
    action: SetupAction,
    scm_environments: dict[str, ScmEnvironment] | None,
    working_dir: Path,
    parameters: SetupParameters,
) -> SetupActionResult:
    """Execute a single SCM_CLONE action.

    Args:
        action: The SCM clone action.
        scm_environments: Dict of SCM-environment plugins.
        working_dir: Working directory (manifest location).
        parameters: Setup parameters.

    Returns:
        The result of the clone operation.
    """
    scm_envs = scm_environments or {}
    if action.installer is None or action.installer not in scm_envs:
        return SetupActionResult(
            action=action, success=False, message=f"SCM environment '{action.installer}' is not available"
        )

    if action.package is None:
        return SetupActionResult(action=action, success=False, message='No repository URL specified')

    scm_env = scm_envs[action.installer]
    url = action.package.name

    # Clone directly into the working directory, not into a derived subdirectory.
    destination = working_dir

    # Skip if already cloned
    if scm_env.is_cloned(url, destination):
        return SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=SkipReason.ALREADY_INSTALLED,
            message=f"Repository already cloned at '{destination}'",
        )

    try:
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, lambda: scm_env.clone(url, destination, dry=parameters.dry_run))
        if success:
            return SetupActionResult(action=action, success=True, message=f"Cloned '{url}' via {action.installer}")
        return SetupActionResult(
            action=action, success=False, message=f"Clone failed for '{url}' via {action.installer}"
        )
    except Exception as e:
        return SetupActionResult(action=action, success=False, message=str(e))
