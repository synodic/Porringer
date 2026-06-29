"""CLI command implementation for self.

Porringer CLI self command module.
"""

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

    info = asyncio.run(api.check_self_updates())

    current = str(info.current_version) if info.current_version else 'unknown'
    latest = str(info.latest_version) if info.latest_version else 'unknown'

    if info.latest_version is None:
        configuration.output.print(
            Panel(
                f'[warning]Could not fetch latest version from PyPI[/warning]\nCurrent version: [info]{current}[/info]',
                title='Version Check',
                border_style='yellow',
            )
        )
        raise typer.Exit(code=1)

    if info.update_available:
        configuration.output.print(
            Panel(
                f'[success]Update available![/success]\n\n'
                f'Current: [info]{current}[/info]\n'
                f'Latest:  [success]{latest}[/success]\n\n',
                title='Porringer Update',
                border_style='green',
            )
        )


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Management of the Porringer instance running the CLI application."""
    pass
