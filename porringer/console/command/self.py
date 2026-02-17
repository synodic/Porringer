"""Porringer CLI self command module."""

import asyncio

import typer
from rich.panel import Panel

from porringer.api import API
from porringer.console.schema import ConsoleConfiguration

app = typer.Typer()


@app.command('check')
def self_check(context: typer.Context) -> None:
    """Checks for updates to the Porringer application.

    Queries PyPI for the latest version and compares with the installed version.
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    api = API(configuration.local_configuration)

    info = asyncio.run(api.check_updates())

    current = str(info.current_version) if info.current_version else 'unknown'
    latest = str(info.latest_version) if info.latest_version else 'unknown'

    if info.latest_version is None:
        configuration.console.print(
            Panel(
                f'[yellow]Could not fetch latest version from PyPI[/yellow]\nCurrent version: [cyan]{current}[/cyan]',
                title='Version Check',
                border_style='yellow',
            )
        )
        raise typer.Exit(code=1)

    if info.update_available:
        configuration.console.print(
            Panel(
                f'[green]Update available![/green]\n\n'
                f'Current: [cyan]{current}[/cyan]\n'
                f'Latest:  [green]{latest}[/green]\n\n',
                title='Porringer Update',
                border_style='green',
            )
        )


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Management of the Porringer instance running the CLI application."""
    pass
