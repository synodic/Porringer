"""Porringer CLI plugin command module"""

import asyncio
from typing import Annotated

import typer

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

    results = asyncio.run(PluginCommands.list())

    if not results:
        configuration.console.print('[yellow]No plugins found[/yellow]')
    else:
        for result in results:
            tool_ver = str(result.tool_version) if result.tool_version else 'n/a'
            status = '[green]installed[/green]' if result.installed else '[red]not installed[/red]'
            configuration.console.print(
                f'{result.name} [{result.kind.value}] v{result.version} (tool: {tool_ver}) {status}'
            )


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
    """Porringer extension management (install, update, remove plugin packages)."""
    pass
