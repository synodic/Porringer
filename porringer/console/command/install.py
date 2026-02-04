"""Porringer CLI install command module"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import (
    APIParameters,
    BatchSetupResults,
    SetupAction,
    SetupActionResult,
    SetupParameters,
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
    """

    path: Path | None = None
    all_cached: bool = False
    dry_run: bool = False
    timeout: int = DEFAULT_TIMEOUT
    fail_fast: bool = True


def _create_api(configuration: Configuration) -> API:
    """Create and return API instance.

    Args:
        configuration: CLI configuration.

    Returns:
        Initialized API instance.
    """
    api_parameters = APIParameters(logging.getLogger('porringer'))
    return API(configuration.local_configuration, api_parameters)


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


def _display_summary(configuration: Configuration, results: BatchSetupResults, dry_run: bool) -> None:
    """Display summary panel.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
    """
    configuration.console.print()

    # Count results by category (exclude plugin checks)
    install_results = [
        r for mr in results.manifest_results for r in mr.results if r.action.action_type.name != 'CHECK_PLUGIN'
    ]

    installed = sum(1 for r in install_results if r.success and not r.skipped)
    skipped = sum(1 for r in install_results if r.success and r.skipped)
    failed = sum(1 for r in install_results if not r.success)
    total = len(install_results)

    if dry_run:
        configuration.console.print(
            Panel(
                f'[dim]Dry run complete.[/dim] {total} action(s) would be executed.',
                border_style='dim',
            )
        )
    elif results.success:
        skip_msg = f', {skipped} skipped' if skipped else ''
        configuration.console.print(
            Panel(
                f'[green]Install complete![/green] {installed} installed{skip_msg}.',
                border_style='green',
            )
        )
    else:
        skip_msg = f', {skipped} skipped' if skipped else ''
        configuration.console.print(
            Panel(
                f'[red]Install failed![/red] {installed} installed{skip_msg}, {failed} failed.',
                border_style='red',
            )
        )


def _display_results(configuration: Configuration, results: BatchSetupResults, dry_run: bool) -> None:
    """Display execution results with arrow-prefixed commands.

    Args:
        configuration: CLI configuration with console.
        results: Batch execution results.
        dry_run: Whether this was a dry run.
    """
    for manifest_result in results.manifest_results:
        configuration.console.print(f'\n[bold]Manifest:[/bold] {manifest_result.manifest_path}')

        displayed_count = 0
        for result in manifest_result.results:
            # Skip plugin checks that passed (internal detail)
            if result.skipped and result.action.action_type.name == 'CHECK_PLUGIN':
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

    _display_summary(configuration, results, dry_run)


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
            paths=None, timeout=options.timeout, fail_fast=options.fail_fast, dry_run=options.dry_run
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
        )
    else:
        # Default to current directory
        setup_params = SetupParameters(
            paths=Path('.').resolve(),
            timeout=options.timeout,
            fail_fast=options.fail_fast,
            dry_run=options.dry_run,
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

    _display_results(configuration, execute_results, options.dry_run)

    if not options.dry_run and not execute_results.success:
        raise typer.Exit(EXIT_FAILURE)


def _execute_with_progress(
    configuration: Configuration,
    api: API,
    preview_results: BatchSetupResults,
    setup_params: SetupParameters,
) -> BatchSetupResults:
    """Execute installation with progress display.

    Args:
        configuration: CLI configuration with console.
        api: The API instance.
        preview_results: Preview results with actions.
        setup_params: Setup parameters.

    Returns:
        BatchSetupResults from execution.
    """
    if setup_params.dry_run:
        # For dry-run, use sync execution without progress
        return api.update.execute_batch(preview_results, setup_params)

    # Count non-check actions for progress
    total_actions = sum(
        1 for mr in preview_results.manifest_results for a in mr.actions if a.action_type.name != 'CHECK_PLUGIN'
    )

    if total_actions == 0:
        return api.update.execute_batch(preview_results, setup_params)

    # Track progress state
    completed = 0
    active_tasks: dict[str, TaskID] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
        TextColumn('({task.completed}/{task.total})'),
        console=configuration.console,
        transient=True,
    ) as progress:
        overall_task = progress.add_task('Installing packages...', total=total_actions)

        def progress_callback(action: SetupAction, result: SetupActionResult | None) -> None:
            nonlocal completed

            if action.action_type.name == 'CHECK_PLUGIN':
                return  # Skip plugin checks in progress

            action_desc = action.package or action.description[:30]

            if result is None:
                # Action starting
                task_id = progress.add_task(f'  {action_desc}', total=1)
                active_tasks[action_desc] = task_id
            else:
                # Action completed
                if action_desc in active_tasks:
                    task_id = active_tasks.pop(action_desc)
                    if result.success:
                        if result.skipped:
                            progress.update(task_id, description=f'  [dim]{action_desc} (skipped)[/dim]', completed=1)
                        else:
                            progress.update(task_id, description=f'  [green]{action_desc}[/green]', completed=1)
                    else:
                        progress.update(task_id, description=f'  [red]{action_desc}[/red]', completed=1)

                completed += 1
                progress.update(overall_task, completed=completed)

        # Run async execution
        async def run_async() -> BatchSetupResults:
            return await api.update.execute_batch_async(preview_results, setup_params, progress_callback)

        return asyncio.run(run_async())


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
) -> None:
    """Install packages from a manifest file.

    Reads the manifest from the specified path (or current directory) and installs
    all packages and runs post-install commands.

    Use --dry-run to preview what would be executed without making changes.
    Use --all to run on all cached directories at once.

    Examples:
        porringer install                        # Run in current directory
        porringer install --path ./my-project    # Run in specific directory
        porringer install --dry-run              # Preview without executing
        porringer install --all                  # Run on all cached directories
    """
    configuration = context.ensure_object(Configuration)

    options = ManifestOptions(
        path=path,
        all_cached=all_cached,
        dry_run=dry_run,
        timeout=timeout,
        fail_fast=fail_fast,
    )

    _handle_manifest(configuration, options)
