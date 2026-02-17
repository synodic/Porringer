"""Porringer CLI plugin command module"""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from porringer.backend.command.plugin import PluginCommands
from porringer.console.schema import ConsoleConfiguration
from porringer.utility.exception import PluginError

app = typer.Typer()


@app.command('list')
def plugin_list(
    context: typer.Context,
) -> None:
    """Lists available plugins

    Args:
        context: The click context
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    results = PluginCommands.list()

    if not results:
        configuration.console.print('[yellow]No plugins found[/yellow]')
    else:
        for result in results:
            tool_ver = str(result.tool_version) if result.tool_version else 'n/a'
            status = '[green]installed[/green]' if result.installed else '[red]not installed[/red]'
            configuration.console.print(
                f'{result.name} [{result.kind.value}] v{result.version} (tool: {tool_ver}) {status}'
            )


@app.command('packages')
def plugin_packages(
    context: typer.Context,
    plugin_name: Annotated[str, typer.Argument(help='Plugin name to query (e.g. pipx, pip, uv)')],
    project_path: Annotated[
        Path,
        typer.Option('--project-path', '-p', help='Project directory for scoped package listing'),
    ] = Path('.'),
) -> None:
    """List packages installed via a specific plugin.

    Queries the named plugin's environment and displays all packages it
    reports as currently installed.  For venv-scoped plugins (pip, uv),
    the listing can be scoped to a project directory.
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    resolved_path = project_path.resolve()

    try:
        packages = PluginCommands.list_packages(plugin_name, resolved_path)
    except PluginError as e:
        configuration.console.print(f'[red]Error: {e.error}[/red]')
        raise typer.Exit(code=1) from None

    if not packages:
        configuration.console.print(f'[yellow]No packages found for plugin: {plugin_name}[/yellow]')
    else:
        table = Table(title=f'Packages ({plugin_name})')
        table.add_column('Name', style='cyan')
        table.add_column('Version', style='green')

        for pkg in sorted(packages, key=lambda p: p.name.lower()):
            table.add_row(pkg.name, pkg.version or 'n/a')

        configuration.console.print(table)


@app.command('install')
def plugin_install(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to install (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Install plugins from PyPI"""
    configuration = context.ensure_object(ConsoleConfiguration)

    for plugin in plugins:
        try:
            result = PluginCommands.install(plugin, dry_run=dry_run)

            if result.success:
                configuration.console.print(f'[green]{result.message}[/green]')
            else:
                configuration.console.print(f'[red]{result.message}[/red]')
                raise typer.Exit(code=1)
        except PluginError as e:
            configuration.console.print(f'[red]Error: {e.error}[/red]')
            raise typer.Exit(code=1) from None


@app.command('update')
def plugin_update(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to update (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Update installed plugins"""
    configuration = context.ensure_object(ConsoleConfiguration)

    results = PluginCommands.update(plugins, dry_run=dry_run)

    has_failure = False
    for result in results:
        if result.success:
            configuration.console.print(f'[green]{result.message}[/green]')
        else:
            configuration.console.print(f'[red]{result.message}[/red]')
            has_failure = True

    if has_failure:
        raise typer.Exit(code=1)


@app.command('uninstall')
def plugin_uninstall(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to remove (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Remove installed plugins"""
    configuration = context.ensure_object(ConsoleConfiguration)

    results = PluginCommands.uninstall(plugins, dry_run=dry_run)

    has_failure = False
    for result in results:
        if result.success:
            configuration.console.print(f'[green]{result.message}[/green]')
        else:
            configuration.console.print(f'[red]{result.message}[/red]')
            has_failure = True

    if has_failure:
        raise typer.Exit(code=1)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Plugin management and operations"""
    pass
