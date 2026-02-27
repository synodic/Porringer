"""Porringer CLI sync command module."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from porringer.api import API
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import (
    BatchSetupResults,
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SubActionProgress,
    SyncStrategy,
)

app = typer.Typer()

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
DEFAULT_TIMEOUT = 300

# Arrow prefix for command display
ARROW = '→'


@dataclass(slots=True)
class _ProgressState:
    """Execution progress state for streaming updates."""

    completed: int = 0
    active_tasks: dict[str, TaskID] = field(default_factory=dict)
    collected_results: list[SetupActionResult] = field(default_factory=list)
    manifests: list[SetupResults] = field(default_factory=list)
    failed_paths: list[tuple[Path, str]] = field(default_factory=list)
    overall_task: TaskID | None = None


@dataclass(slots=True)
class _SyncOptions:
    """Bundled options for manifest sync execution."""

    path: Path | None = None
    all_cached: bool = False
    dry_run: bool = False
    timeout: int = DEFAULT_TIMEOUT
    fail_fast: bool = True
    strategy: SyncStrategy = SyncStrategy.MINIMAL
    project_directory: Path | None = None
    plugins: list[str] | None = None


def _create_api(configuration: ConsoleConfiguration) -> API:
    """Create and return API instance.

    Args:
        configuration: CLI configuration.

    Returns:
        Initialized API instance.
    """
    return API(configuration.local_configuration)


def _progress_label(strategy: SyncStrategy) -> str:
    """Get the progress label based on sync strategy."""
    if strategy == SyncStrategy.LATEST:
        return 'Upgrading packages...'
    if strategy == SyncStrategy.EXACT:
        return 'Ensuring packages...'
    return 'Syncing packages...'


def _action_description(action: SetupAction) -> str:
    """Build a short description for progress output."""
    return str(action.package) if action.package else action.description[:30]


@dataclass(slots=True)
class _ProgressTracker:
    """Tracks progress during streaming execution, reducing parameter passing."""

    progress: Progress
    setup_params: SetupParameters
    state: _ProgressState

    def handle_action_started(self, action_desc: str, total_actions: int) -> None:
        """Record that an action has started."""
        if total_actions > 0 and not self.setup_params.dry_run:
            task_id = self.progress.add_task(f'  {action_desc}', total=1)
            self.state.active_tasks[action_desc] = task_id

    def handle_action_completed(
        self,
        action_desc: str,
        result: SetupActionResult | None,
        total_actions: int,
    ) -> None:
        """Record that an action has completed and update the progress bar."""
        if result:
            self.state.collected_results.append(result)

        if action_desc in self.state.active_tasks:
            task_id = self.state.active_tasks.pop(action_desc)
            if result and result.success:
                if result.skipped:
                    self.progress.update(task_id, description=f'  [dim]{action_desc} (skipped)[/dim]', completed=1)
                else:
                    self.progress.update(task_id, description=f'  [green]{action_desc}[/green]', completed=1)
            else:
                self.progress.update(task_id, description=f'  [red]{action_desc}[/red]', completed=1)

        self.state.completed += 1
        overall_task = self.state.overall_task
        if total_actions > 0 and not self.setup_params.dry_run and overall_task is not None:
            self.progress.update(overall_task, completed=self.state.completed)

    def handle_sub_action_progress(self, action_desc: str, sub: SubActionProgress | None) -> None:
        """Update the progress bar with sub-action detail."""
        if sub is None or action_desc not in self.state.active_tasks:
            return

        task_id = self.state.active_tasks[action_desc]
        phase = sub.phase

        desc = f'  {action_desc} [{phase}] {sub.message}' if sub.message else f'  {action_desc} [{phase}]'

        max_desc_len = 80
        if len(desc) > max_desc_len:
            desc = desc[: max_desc_len - 3] + '...'

        if sub.progress is not None:
            self.progress.update(task_id, description=desc, completed=sub.progress, total=1.0)
        else:
            self.progress.update(task_id, description=desc)

    def handle_progress_event(self, event: ProgressEvent, total_actions: int) -> None:
        """Dispatch a progress event to the appropriate handler."""
        if event.action is None:
            return
        action_desc = _action_description(event.action)

        if event.kind == ProgressEventKind.ACTION_STARTED:
            self.handle_action_started(action_desc, total_actions)
            return
        if event.kind == ProgressEventKind.ACTION_COMPLETED:
            self.handle_action_completed(action_desc, event.result, total_actions)
            return
        if event.kind == ProgressEventKind.SUB_ACTION_PROGRESS:
            self.handle_sub_action_progress(action_desc, event.sub_action)


async def _run_stream_with_progress(api: API, tracker: _ProgressTracker) -> None:
    """Stream sync events and update progress display.

    Args:
        api: The API instance.
        tracker: Progress tracker with state and display.
    """
    async for event in api.sync.execute_stream(tracker.setup_params):
        if event.kind == ProgressEventKind.MANIFEST_LOADED and event.manifest:
            tracker.state.manifests.append(event.manifest)
            total = sum(len(m.actions) for m in tracker.state.manifests)
            if tracker.state.overall_task is not None:
                tracker.progress.update(tracker.state.overall_task, total=total)
            elif total > 0 and not tracker.setup_params.dry_run:
                tracker.state.overall_task = tracker.progress.add_task(
                    _progress_label(tracker.setup_params.strategy), total=total
                )
            continue
        if event.kind == ProgressEventKind.MANIFEST_FAILED and event.failed_path:
            tracker.state.failed_paths.append(event.failed_path)
            continue
        total_actions = sum(len(m.actions) for m in tracker.state.manifests)
        tracker.handle_progress_event(event, total_actions)


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
    configuration: ConsoleConfiguration,
    results: BatchSetupResults,
    dry_run: bool,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
) -> None:
    """Display summary panel.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
        strategy: The sync strategy.
    """
    configuration.console.print()

    # Count results by category
    package_results = [r for mr in results.manifest_results for r in mr.results]

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
        # Use strategy to determine the verb
        detail = (
            f'{succeeded} upgraded' if strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT} else f'{succeeded} synced'
        )
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
    configuration: ConsoleConfiguration,
    results: BatchSetupResults,
    dry_run: bool,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
) -> None:
    """Display execution results with arrow-prefixed commands.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
        strategy: The sync strategy.
    """
    for manifest_result in results.manifest_results:
        configuration.console.print(f'\n[bold]Manifest:[/bold] {manifest_result.manifest_path}')

        displayed_count = 0
        for result in manifest_result.results:
            displayed_count += 1
            command_str = _format_cli_command(result)

            if result.skipped and result.skip_reason:
                # Show skipped packages with reason (e.g., already installed)
                configuration.console.print(f'  [dim]{ARROW} {command_str}[/dim]')
                if result.message:
                    configuration.console.print(f'    [dim italic]{result.message}[/dim italic]')
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

    _display_summary(configuration, results, dry_run, strategy)


def _handle_manifest(configuration: ConsoleConfiguration, options: _SyncOptions) -> None:
    """Handle manifest install execution.

    Args:
        configuration: CLI configuration.
        options: Bundled sync options.

    Raises:
        typer.Exit: On error.
    """
    api = _create_api(configuration)

    # Determine what paths to use
    if options.all_cached:
        setup_params = SetupParameters(
            paths=None,
            project_directory=options.project_directory,
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            strategy=options.strategy,
            plugins=options.plugins,
        )
    elif options.path:
        if not options.path.exists():
            configuration.console.print(f'[red]Error:[/red] Path does not exist: {options.path}')
            raise typer.Exit(EXIT_FAILURE)
        resolved_path = options.path.resolve()
        setup_params = SetupParameters(
            paths=resolved_path,
            project_directory=options.project_directory,
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            strategy=options.strategy,
            plugins=options.plugins,
        )
    else:
        # Default to current directory
        setup_params = SetupParameters(
            paths=Path('.').resolve(),
            project_directory=options.project_directory,
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
            strategy=options.strategy,
            plugins=options.plugins,
        )

    # For dry runs, use the simple sync method (no progress bar needed).
    # For real execution, use the streaming progress display.
    try:
        if setup_params.dry_run:
            execute_results = api.sync.run(setup_params)
        else:
            execute_results = _execute_with_progress(configuration, api, setup_params)
    except ValueError as e:
        configuration.console.print(f'[red]Error:[/red] {e}')
        raise typer.Exit(EXIT_FAILURE) from e

    # Fast path: all manifests failed, no actions at all
    if execute_results.failed_paths and execute_results.total_actions == 0:
        for _path, error in execute_results.failed_paths:
            typer.echo(f'Error: {error}', err=True)
        raise typer.Exit(EXIT_FAILURE)

    if execute_results.total_actions == 0:
        configuration.console.print('[yellow]No actions to execute[/yellow]')
        return

    _display_results(configuration, execute_results, options.dry_run, options.strategy)

    if not execute_results.success:
        raise typer.Exit(EXIT_FAILURE)


def _execute_with_progress(
    configuration: ConsoleConfiguration,
    api: API,
    setup_params: SetupParameters,
) -> BatchSetupResults:
    """Execute installation with progress display.

    Uses `execute_stream` to receive `ProgressEvent` items and updates
    a Rich progress bar accordingly.  Manifests are discovered via
    `MANIFEST_LOADED` events emitted by the stream — no separate preview
    step is required.

    Args:
        configuration: CLI configuration with console.
        api: The API instance.
        setup_params: Setup parameters.

    Returns:
        BatchSetupResults from execution.
    """
    state = _ProgressState()

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
        TextColumn('({task.completed}/{task.total})'),
        console=configuration.console,
        transient=True,
        disable=setup_params.dry_run,
    ) as progress:
        tracker = _ProgressTracker(progress=progress, setup_params=setup_params, state=state)
        asyncio.run(_run_stream_with_progress(api, tracker))

    # Build BatchSetupResults from collected events
    # Partition results by manifest using action identity
    manifest_action_sets = [set(id(a) for a in m.actions) for m in state.manifests]
    manifest_results: list[SetupResults] = []

    for preview, action_ids in zip(state.manifests, manifest_action_sets, strict=False):
        mr_results = [r for r in state.collected_results if id(r.action) in action_ids]
        sr = SetupResults(
            actions=preview.actions,
            results=mr_results,
            manifest_path=preview.manifest_path,
            root_directory=preview.root_directory,
            metadata=preview.metadata,
        )
        manifest_results.append(sr)

    return BatchSetupResults(manifest_results=manifest_results, failed_paths=state.failed_paths)


@app.callback(invoke_without_command=True)
def sync_default(
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
    project_dir: Annotated[
        Path | None,
        typer.Option(
            '--project-dir',
            '-d',
            help='Working directory for project-sync and post-sync actions (inferred from --path by default)',
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
        typer.Option('--timeout', '-t', help='Timeout in seconds for post-sync commands'),
    ] = DEFAULT_TIMEOUT,
    fail_fast: Annotated[
        bool,
        typer.Option('--fail-fast/--no-fail-fast', help='Stop on first error'),
    ] = True,
    strategy: Annotated[
        str,
        typer.Option(
            '--strategy',
            '-s',
            help='Sync strategy: minimal (default), latest, or exact',
        ),
    ] = 'minimal',
    plugin: Annotated[
        list[str] | None,
        typer.Option(
            '--plugin',
            help='Only include actions from these plugins (repeatable). Omit to include all.',
        ),
    ] = None,
) -> None:
    """Synchronise the local environment with a manifest.

    Reads the manifest from the specified path (or current directory) and
    installs or upgrades packages according to the chosen strategy.

    When --project-dir is omitted, project-sync and post-sync commands run in
    the manifest's parent directory.  Pass --project-dir explicitly to override,
    or use the API with `project_directory=False` to skip project backends.

    Strategies:
      minimal — Install packages that aren't already present (default).
      latest  — Upgrade all packages; fall back to install if not present.
      exact   — Check each package; upgrade if installed, install if not.

    Use --dry-run to preview what would be executed without making changes.
    Use --all to run on all cached directories at once.

    Examples:
        porringer sync                                  # Sync current directory
        porringer sync --strategy latest --all          # Upgrade all cached manifests
        porringer sync --strategy exact --path ./x      # Ensure exact in directory
        porringer sync --dry-run                        # Preview without executing
        porringer sync --path m.json --project-dir ./p  # Separate manifest & project
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    # Parse strategy string to enum
    strategy_map = {'minimal': SyncStrategy.MINIMAL, 'latest': SyncStrategy.LATEST, 'exact': SyncStrategy.EXACT}
    sync_strategy = strategy_map.get(strategy.lower())
    if sync_strategy is None:
        configuration.console.print(f"[red]Error:[/red] Invalid strategy '{strategy}'. Use: minimal, latest, or exact")
        raise typer.Exit(EXIT_FAILURE)

    _handle_manifest(
        configuration,
        _SyncOptions(
            path=path,
            all_cached=all_cached,
            dry_run=dry_run,
            timeout=timeout,
            fail_fast=fail_fast,
            strategy=sync_strategy,
            project_directory=project_dir.resolve() if project_dir else None,
            plugins=plugin if plugin else None,
        ),
    )
