"""Porringer CLI install command module"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import (
    BatchSetupResults,
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupActionType,
    SetupMode,
    SetupParameters,
    SetupResults,
    SubActionProgress,
)
from porringer.utility.exception import ManifestError

app = typer.Typer()

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
DEFAULT_TIMEOUT = 300

# Arrow prefix for command display
ARROW = '→'


@dataclass
class ManifestOptions:
    """Options for manifest install operations.

    Attributes:
        path: Path to manifest file or directory.
        all_cached: Use all cached directories.
        dry_run: Preview without executing.
        timeout: Timeout in seconds for commands.
        fail_fast: Stop on first error.
        mode: Execution mode (install, upgrade, or ensure).
    """

    path: Path | None = None
    all_cached: bool = False
    dry_run: bool = False
    timeout: int = DEFAULT_TIMEOUT
    fail_fast: bool = True
    mode: SetupMode = SetupMode.INSTALL


@dataclass
class _ProgressState:
    """Execution progress state for streaming updates."""

    completed: int = 0
    active_tasks: dict[str, TaskID] = field(default_factory=dict)
    collected_results: list[SetupActionResult] = field(default_factory=list)


def _create_api(configuration: Configuration) -> API:
    """Create and return API instance.

    Args:
        configuration: CLI configuration.

    Returns:
        Initialized API instance.
    """
    return API(configuration.local_configuration)


def _count_non_check_actions(preview_results: BatchSetupResults) -> int:
    """Count actions excluding plugin availability checks."""
    return sum(
        1
        for mr in preview_results.manifest_results
        for action in mr.actions
        if action.action_type != SetupActionType.CHECK_PLUGIN
    )


def _progress_label(mode: SetupMode) -> str:
    """Get the progress label based on setup mode."""
    if mode == SetupMode.UPGRADE:
        return 'Upgrading packages...'
    if mode == SetupMode.ENSURE:
        return 'Ensuring packages...'
    return 'Installing packages...'


def _action_description(action: SetupAction) -> str:
    """Build a short description for progress output."""
    return str(action.package) if action.package else action.description[:30]


def _handle_action_started(
    action_desc: str,
    progress: Progress,
    setup_params: SetupParameters,
    total_actions: int,
    state: _ProgressState,
) -> None:
    if total_actions > 0 and not setup_params.dry_run:
        task_id = progress.add_task(f'  {action_desc}', total=1)
        state.active_tasks[action_desc] = task_id


def _handle_action_completed(
    action_desc: str,
    result: SetupActionResult | None,
    progress: Progress,
    setup_params: SetupParameters,
    total_actions: int,
    overall_task: TaskID | None,
    state: _ProgressState,
) -> None:
    if result:
        state.collected_results.append(result)

    if action_desc in state.active_tasks:
        task_id = state.active_tasks.pop(action_desc)
        if result and result.success:
            if result.skipped:
                progress.update(task_id, description=f'  [dim]{action_desc} (skipped)[/dim]', completed=1)
            else:
                progress.update(task_id, description=f'  [green]{action_desc}[/green]', completed=1)
        else:
            progress.update(task_id, description=f'  [red]{action_desc}[/red]', completed=1)

    state.completed += 1
    if total_actions > 0 and not setup_params.dry_run and overall_task is not None:
        progress.update(overall_task, completed=state.completed)


def _handle_sub_action_progress(
    action_desc: str,
    sub: SubActionProgress | None,
    progress: Progress,
    state: _ProgressState,
) -> None:
    if sub is None or action_desc not in state.active_tasks:
        return

    task_id = state.active_tasks[action_desc]
    phase = sub.phase

    desc = f'  {action_desc} [{phase}] {sub.message}' if sub.message else f'  {action_desc} [{phase}]'

    max_desc_len = 80
    if len(desc) > max_desc_len:
        desc = desc[: max_desc_len - 3] + '...'

    if sub.progress is not None:
        progress.update(task_id, description=desc, completed=sub.progress, total=1.0)
    else:
        progress.update(task_id, description=desc)


def _handle_progress_event(
    event: ProgressEvent,
    progress: Progress,
    setup_params: SetupParameters,
    total_actions: int,
    overall_task: TaskID | None,
    state: _ProgressState,
) -> None:
    if event.action.action_type == SetupActionType.CHECK_PLUGIN:
        if event.kind == ProgressEventKind.ACTION_COMPLETED and event.result:
            state.collected_results.append(event.result)
        return

    action_desc = _action_description(event.action)

    if event.kind == ProgressEventKind.ACTION_STARTED:
        _handle_action_started(action_desc, progress, setup_params, total_actions, state)
        return
    if event.kind == ProgressEventKind.ACTION_COMPLETED:
        _handle_action_completed(
            action_desc,
            event.result,
            progress,
            setup_params,
            total_actions,
            overall_task,
            state,
        )
        return
    if event.kind == ProgressEventKind.SUB_ACTION_PROGRESS:
        _handle_sub_action_progress(action_desc, event.sub_action, progress, state)


async def _run_stream_with_progress(
    api: API,
    preview_results: BatchSetupResults,
    setup_params: SetupParameters,
    progress: Progress,
    total_actions: int,
    overall_task: TaskID | None,
    state: _ProgressState,
) -> None:
    async for event in api.update.execute_stream(preview_results, setup_params):
        _handle_progress_event(event, progress, setup_params, total_actions, overall_task, state)


def _format_cli_command(result: SetupActionResult) -> str:
    """Format an action result as a CLI command string.

    Args:
        result: The action result.

    Returns:
        Formatted command string with arrow prefix.
    """
    action = result.action
    if action.cli_command:
        return ' '.join(action.cli_command)
    # Fallback to description if no CLI command
    return action.description


def _display_summary(
    configuration: Configuration, results: BatchSetupResults, dry_run: bool, mode: SetupMode = SetupMode.INSTALL
) -> None:
    """Display summary panel.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
        mode: The execution mode.
    """
    configuration.console.print()

    # Count results by category (exclude plugin checks)
    package_results = [
        r for mr in results.manifest_results for r in mr.results if r.action.action_type != SetupActionType.CHECK_PLUGIN
    ]

    succeeded = sum(1 for r in package_results if r.success and not r.skipped)
    skipped = sum(1 for r in package_results if r.success and r.skipped)
    failed = sum(1 for r in package_results if not r.success)
    total = len(package_results)

    if dry_run:
        configuration.console.print(
            Panel(
                f'[dim]Dry run complete.[/dim] {total} action(s) would be executed.',
                border_style='dim',
            )
        )
    elif results.success:
        skip_msg = f', {skipped} skipped' if skipped else ''
        # Use mode to determine the verb
        detail = f'{succeeded} upgraded' if mode in {SetupMode.UPGRADE, SetupMode.ENSURE} else f'{succeeded} installed'
        configuration.console.print(
            Panel(
                f'[green]Complete![/green] {detail}{skip_msg}.',
                border_style='green',
            )
        )
    else:
        skip_msg = f', {skipped} skipped' if skipped else ''
        configuration.console.print(
            Panel(
                f'[red]Failed![/red] {succeeded} succeeded{skip_msg}, {failed} failed.',
                border_style='red',
            )
        )


def _display_results(
    configuration: Configuration, results: BatchSetupResults, dry_run: bool, mode: SetupMode = SetupMode.INSTALL
) -> None:
    """Display execution results with arrow-prefixed commands.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
        mode: The execution mode.
    """
    for manifest_result in results.manifest_results:
        configuration.console.print(f'\n[bold]Manifest:[/bold] {manifest_result.manifest_path}')

        displayed_count = 0
        for result in manifest_result.results:
            # Skip plugin checks that passed (internal detail)
            if result.skipped and result.action.action_type == SetupActionType.CHECK_PLUGIN:
                continue

            displayed_count += 1
            command_str = _format_cli_command(result)

            if result.skipped and result.skip_reason:
                # Show skipped packages with reason (e.g., already installed)
                configuration.console.print(f'  [dim]{ARROW} {command_str}[/dim]')
                configuration.console.print(f'    [dim italic]{result.skip_reason}[/dim italic]')
            elif result.success:
                if dry_run:
                    configuration.console.print(f'  [dim]{ARROW}[/dim] {command_str}')
                else:
                    configuration.console.print(f'  [green]{ARROW}[/green] {command_str}')
            else:
                configuration.console.print(f'  [red]{ARROW}[/red] {command_str}')
                if result.message:
                    configuration.console.print(f'    [dim]{result.message}[/dim]')

        if displayed_count == 0:
            configuration.console.print('  [dim]No actions to perform[/dim]')

    for path, error in results.failed_paths:
        configuration.console.print(f'\n[red]Failed:[/red] {path}')
        configuration.console.print(f'  [dim]{error}[/dim]')

    _display_summary(configuration, results, dry_run, mode)


def _handle_manifest(configuration: Configuration, options: ManifestOptions) -> None:
    """Handle manifest install execution.

    Args:
        configuration: CLI configuration.
        options: Manifest options.

    Raises:
        typer.Exit: On error.
    """
    api = _create_api(configuration)

    # Determine what paths to use
    if options.all_cached:
        setup_params = SetupParameters(
            paths=None,
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            mode=options.mode,
        )
    elif options.path:
        if not options.path.exists():
            configuration.console.print(f'[red]Error:[/red] Path does not exist: {options.path}')
            raise typer.Exit(EXIT_FAILURE)
        setup_params = SetupParameters(
            paths=options.path.resolve(),
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            mode=options.mode,
        )
    else:
        # Default to current directory
        setup_params = SetupParameters(
            paths=Path('.').resolve(),
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            mode=options.mode,
        )

    # Preview to get actions
    try:
        preview_results = api.update.preview_batch(setup_params)
    except (ManifestError, ValueError) as e:
        error_msg = e.error if isinstance(e, ManifestError) else str(e)
        configuration.console.print(f'[red]Error:[/red] {error_msg}')
        raise typer.Exit(EXIT_FAILURE) from e

    # Check for failed paths (no manifest found)
    if preview_results.failed_paths and not preview_results.manifest_results:
        for _path, error in preview_results.failed_paths:
            configuration.console.print(f'[red]Error:[/red] {error}')
        raise typer.Exit(EXIT_FAILURE)

    if preview_results.total_actions == 0 and not preview_results.failed_paths:
        configuration.console.print('[yellow]No actions to execute[/yellow]')
        return

    # Execute with async progress
    execute_results = _execute_with_progress(configuration, api, preview_results, setup_params)

    _display_results(configuration, execute_results, options.dry_run, options.mode)

    if not options.dry_run and not execute_results.success:
        raise typer.Exit(EXIT_FAILURE)


def _execute_with_progress(
    configuration: Configuration,
    api: API,
    preview_results: BatchSetupResults,
    setup_params: SetupParameters,
) -> BatchSetupResults:
    """Execute installation with progress display.

    Uses ``execute_stream`` to receive ``ProgressEvent`` items and updates
    a Rich progress bar accordingly.  Results are collected from the stream
    events so no second execution is needed.

    Args:
        configuration: CLI configuration with console.
        api: The API instance.
        preview_results: Preview results with actions.
        setup_params: Setup parameters.

    Returns:
        BatchSetupResults from execution.
    """
    total_actions = _count_non_check_actions(preview_results)
    state = _ProgressState()

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
        TextColumn('({task.completed}/{task.total})'),
        console=configuration.console,
        transient=True,
        disable=total_actions == 0 or setup_params.dry_run,
    ) as progress:
        overall_task = None
        if total_actions > 0 and not setup_params.dry_run:
            overall_task = progress.add_task(_progress_label(setup_params.mode), total=total_actions)

        asyncio.run(
            _run_stream_with_progress(
                api,
                preview_results,
                setup_params,
                progress,
                total_actions,
                overall_task,
                state,
            )
        )

    # Build BatchSetupResults from collected events
    manifest_results: list[SetupResults] = []
    for preview in preview_results.manifest_results:
        sr = SetupResults(actions=preview.actions, results=list(state.collected_results))
        sr.manifest_path = preview.manifest_path
        manifest_results.append(sr)

    return BatchSetupResults(
        manifest_results=manifest_results,
        failed_paths=list(preview_results.failed_paths),
    )


@app.callback(invoke_without_command=True)
def install_default(
    context: typer.Context,
    *,
    path: Annotated[
        Path | None,
        typer.Option(
            '--path',
            '-p',
            help='Path to manifest file (porringer.json) or directory containing one',
        ),
    ] = None,
    all_cached: Annotated[
        bool,
        typer.Option('--all', '-a', help='Run on all cached directories'),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option('--dry-run', '-n', help='Preview actions without executing them'),
    ] = False,
    timeout: Annotated[
        int,
        typer.Option('--timeout', '-t', help='Timeout in seconds for post-install commands'),
    ] = DEFAULT_TIMEOUT,
    fail_fast: Annotated[
        bool,
        typer.Option('--fail-fast/--no-fail-fast', help='Stop on first error'),
    ] = True,
    mode: Annotated[
        str,
        typer.Option(
            '--mode',
            '-m',
            help='Execution mode: install (default), upgrade, or ensure',
        ),
    ] = 'install',
) -> None:
    """Install or upgrade packages from a manifest file.

    Reads the manifest from the specified path (or current directory) and
    installs or upgrades packages according to the chosen mode.

    Modes:
      install  — Install packages that aren't already present (default).
      upgrade  — Upgrade all packages; fall back to install if not present.
      ensure   — Check each package; upgrade if installed, install if not.

    Use --dry-run to preview what would be executed without making changes.
    Use --all to run on all cached directories at once.

    Examples:
        porringer install                            # Install in current directory
        porringer install --mode upgrade --all       # Upgrade all cached manifests
        porringer install --mode ensure --path ./x   # Ensure latest in directory
        porringer install --dry-run                  # Preview without executing
    """
    configuration = context.ensure_object(Configuration)

    # Parse mode string to enum
    mode_map = {'install': SetupMode.INSTALL, 'upgrade': SetupMode.UPGRADE, 'ensure': SetupMode.ENSURE}
    setup_mode = mode_map.get(mode.lower())
    if setup_mode is None:
        configuration.console.print(f"[red]Error:[/red] Invalid mode '{mode}'. Use: install, upgrade, or ensure")
        raise typer.Exit(EXIT_FAILURE)

    options = ManifestOptions(
        path=path,
        all_cached=all_cached,
        dry_run=dry_run,
        timeout=timeout,
        fail_fast=fail_fast,
        mode=setup_mode,
    )

    _handle_manifest(configuration, options)
