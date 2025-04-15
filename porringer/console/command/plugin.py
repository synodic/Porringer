"""Porringer CLI plugin command module"""

import logging
from typing import Annotated

import typer

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import APIParameters, ListPluginsParameters

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

    api_parameters = APIParameters(logging.getLogger('porringer'))
    api = API(configuration.local_configuration, api_parameters)

    list_parameters = ListPluginsParameters()
    results = api.plugin.list(list_parameters)

    for result in results:
        configuration.console.print(result)


@app.command('install')
def plugin_install(
    context: typer.Context, plugins: Annotated[list[str], typer.Argument(help='Plugins to install')]
) -> None:
    """Install plugins"""
    for plugin in plugins:
        pass


@app.command('update')
def plugin_update(
    context: typer.Context, plugins: Annotated[list[str], typer.Argument(help='Plugins to update')]
) -> None:
    """Update plugins"""
    for plugin in plugins:
        pass


@app.command('uninstall')
def plugin_uninstall(
    context: typer.Context, plugins: Annotated[list[str], typer.Argument(help='Plugins to remove')]
) -> None:
    """Remove installed plugins"""
    for plugin in plugins:
        pass


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Plugin management and operations"""
    pass
