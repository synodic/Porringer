"""Porringer CLI setup command module"""

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import (
    APIParameters,
    SetupAction,
    SetupParameters,
)
from porringer.utility.exception import ManifestError

app = typer.Typer()


def _format_action_table(actions: list[SetupAction]) -> Table:
    """Formats actions as a rich table for display.

    Args:
        actions: The list of actions to format.

    Returns:
        A rich Table for display.
    """
    table = Table(title='Setup Actions', show_header=True, header_style='bold magenta')
    table.add_column('#', style='dim', width=4)
    table.add_column('Type', style='cyan', width=15)
    table.add_column('Description', style='white')

    for i, action in enumerate(actions, 1):
        type_name = action.action_type.name.replace('_', ' ').title()
        table.add_row(str(i), type_name, action.description)

    return table


@app.command('preview')
def setup_preview(
    context: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            help='Path to manifest file (.porringer) or directory containing one',
            exists=True,
            resolve_path=True,
        ),
    ] = Path('.'),
) -> None:
    """Preview setup actions without executing them.

    Reads the manifest from the specified path and displays all actions
    that would be performed during setup.

    Args:
        context: The typer context.
        path: Path to manifest file or directory.
    """
    configuration = context.ensure_object(Configuration)

    api_parameters = APIParameters(logging.getLogger('porringer'))
    api = API(configuration.local_configuration, api_parameters)

    setup_params = SetupParameters(path=path)

    try:
        results = api.setup.preview(setup_params)
    except ManifestError as e:
        configuration.console.print(f'[red]Error:[/red] {e.error}')
        raise typer.Exit(1) from e

    configuration.console.print(f'\n[bold]Manifest:[/bold] {results.manifest_path}\n')

    if not results.actions:
        configuration.console.print('[yellow]No actions defined in manifest[/yellow]')
        return

    table = _format_action_table(results.actions)
    configuration.console.print(table)

    configuration.console.print(
        f'\n[dim]Total: {len(results.actions)} action(s). Run [bold]porringer setup run[/bold] to execute.[/dim]'
    )


@app.command('run')
def setup_run(
    context: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            help='Path to manifest file (porringer.json) or directory containing one',
            exists=True,
            resolve_path=True,
        ),
    ] = Path('.'),
    yes: Annotated[
        bool,
        typer.Option('--yes', '-y', help='Skip confirmation prompt'),
    ] = False,
    timeout: Annotated[
        int,
        typer.Option('--timeout', '-t', help='Timeout in seconds for post-install commands'),
    ] = 300,
) -> None:
    """Execute setup actions from the manifest.

    First previews all actions, then prompts for confirmation before executing.
    Use --yes to skip the confirmation prompt (useful for CI/automation).

    Args:
        context: The typer context.
        path: Path to manifest file or directory.
        yes: Skip confirmation prompt.
        timeout: Timeout in seconds for commands.
    """
    configuration = context.ensure_object(Configuration)

    api_parameters = APIParameters(logging.getLogger('porringer'))
    api = API(configuration.local_configuration, api_parameters)

    setup_params = SetupParameters(path=path, timeout=timeout)

    # First, preview
    try:
        preview_results = api.setup.preview(setup_params)
    except ManifestError as e:
        configuration.console.print(f'[red]Error:[/red] {e.error}')
        raise typer.Exit(1) from e

    configuration.console.print(f'\n[bold]Manifest:[/bold] {preview_results.manifest_path}\n')

    if not preview_results.actions:
        configuration.console.print('[yellow]No actions defined in manifest[/yellow]')
        return

    # Show preview
    table = _format_action_table(preview_results.actions)
    configuration.console.print(table)
    configuration.console.print()

    # Confirm unless --yes
    if not yes:
        confirmed = typer.confirm(f'Execute {len(preview_results.actions)} action(s)?', default=False)
        if not confirmed:
            configuration.console.print('[yellow]Aborted[/yellow]')
            raise typer.Exit(0)

    # Execute
    configuration.console.print('\n[bold]Executing setup...[/bold]\n')

    execute_results = api.setup.execute(preview_results.actions, setup_params)

    # Display results
    success_count = sum(1 for r in execute_results.results if r.success)
    fail_count = len(execute_results.results) - success_count

    for result in execute_results.results:
        if result.success:
            configuration.console.print(f'  [green]✓[/green] {result.action.description}')
        else:
            configuration.console.print(f'  [red]✗[/red] {result.action.description}')
            if result.message:
                configuration.console.print(f'    [dim]{result.message}[/dim]')

    configuration.console.print()

    if fail_count == 0:
        configuration.console.print(
            Panel(f'[green]Setup complete![/green] {success_count} action(s) succeeded.', border_style='green')
        )
    else:
        configuration.console.print(
            Panel(
                f'[red]Setup failed![/red] {success_count} succeeded, {fail_count} failed.',
                border_style='red',
            )
        )
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def setup_default(
    context: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(
            help='Path to manifest file (porringer.json) or directory containing one',
        ),
    ] = None,
) -> None:
    """Project setup from manifest files.

    Without a subcommand, defaults to 'preview' to show actions without executing.
    Use 'porringer setup run' to execute setup actions.
    """
    if context.invoked_subcommand is None:
        # Default to preview if no subcommand given
        if path is None:
            path = Path('.')

        if not path.exists():
            configuration = context.ensure_object(Configuration)
            configuration.console.print(f'[red]Error:[/red] Path does not exist: {path}')
            raise typer.Exit(1)

        context.invoke(setup_preview, context=context, path=path)
