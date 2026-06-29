"""CLI command implementation for check."""

"""Porringer CLI check command module for checking package updates via plugins."""

import asyncio
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from porringer.backend.command.package import PackageCommands
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import (
    CheckParameters,
    CheckResult,
)

app = typer.Typer()


def _display_results(configuration: ConsoleConfiguration, results: list[CheckResult]) -> None:
    """Display check results to the console.

    Args:
        configuration: CLI configuration.
        results: List of check results.
    """
    total_updates = sum(r.updates_available for r in results if r.success)

    if total_updates == 0:
        configuration.output.print(Panel('[muted]All packages are up to date.[/muted]', border_style='dim'))
        return

    configuration.output.print(f'\n[heading][success]{total_updates} update(s) available[/success][/heading]\n')

    for result in results:
        if not result.success:
            configuration.output.print(f'[warning]{result.plugin}:[/warning] [error]Error: {result.error}[/error]')
            continue

        if not result.packages:
            continue

        table = Table(title=f'[heading]{result.plugin}[/heading]', show_header=True)
        table.add_column('Package', style='cyan')
        table.add_column('Current', style='dim')
        table.add_column('Latest', style='green')

        for pkg in result.packages:
            table.add_row(
                pkg.name,
                str(pkg.current_version) if pkg.current_version else 'N/A',
                str(pkg.latest_version) if pkg.latest_version else 'N/A',
            )

        configuration.output.print(table)
        configuration.output.blank()


@app.callback(invoke_without_command=True)
def check_default(
    context: typer.Context,
    *,
    plugin: Annotated[
        list[str] | None,
        typer.Option('--plugin', '-p', help='Plugin(s) to check. Omit to check all.'),
    ] = None,
    include_prereleases: Annotated[
        bool,
        typer.Option('--prereleases', help='Include pre-release versions'),
    ] = False,
) -> None:
    """Check for available package updates via plugins.

    Each plugin uses its native tooling to check for updates.

    Examples:
        porringer check
        porringer check --plugin pip
        porringer check --plugin pip --plugin pipx --prereleases
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    params = CheckParameters(
        plugins=plugin,
        include_prereleases=include_prereleases,
    )

    results = asyncio.run(PackageCommands.check_updates(params))
    _display_results(configuration, results)
