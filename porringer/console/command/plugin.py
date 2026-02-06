"""Porringer CLI plugin command module"""

from typing import Annotated

import typer

from porringer.api import API
from porringer.backend.schema import (
    PluginInstallParameters,
    PluginUninstallParameters,
    PluginUpdateParameters,
)
from porringer.console.schema import Configuration
from porringer.schema import ListPluginsParameters
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
    configuration = context.ensure_object(Configuration)

    api = API(configuration.local_configuration)

    list_parameters = ListPluginsParameters()
    results = api.plugin.list(list_parameters)

    if not results:
        configuration.console.print('[yellow]No plugins found[/yellow]')
    else:
        for result in results:
            configuration.console.print(result)


@app.command('install')
def plugin_install(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to install (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Install plugins from PyPI"""
    configuration = context.ensure_object(Configuration)

    api = API(configuration.local_configuration)

    for plugin in plugins:
        try:
            params = PluginInstallParameters(name=plugin, dry=dry_run)
            result = api.plugin.install(params)

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
    configuration = context.ensure_object(Configuration)

    api = API(configuration.local_configuration)

    params = PluginUpdateParameters(names=plugins, dry=dry_run)
    results = api.plugin.update(params)

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
    configuration = context.ensure_object(Configuration)

    api = API(configuration.local_configuration)

    params = PluginUninstallParameters(names=plugins, dry=dry_run)
    results = api.plugin.uninstall(params)

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
