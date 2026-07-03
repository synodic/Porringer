"""CLI command implementation for plugin.

Porringer CLI plugin command module.
"""

import asyncio

import typer

from porringer.backend.command.plugin import PluginCommands
from porringer.console.schema import ConsoleConfiguration

app = typer.Typer()


@app.command('list')
def plugin_list(
    context: typer.Context,
) -> None:
    """Lists available plugins.

    Args:
        context: The click context
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    results = asyncio.run(PluginCommands.list())

    if not results:
        configuration.output.warning('No plugins found')
    else:
        for result in results:
            tool_ver = str(result.tool_version) if result.tool_version else 'n/a'
            status = '[success]installed[/success]' if result.installed else '[error]not installed[/error]'
            configuration.output.print(
                f'{result.name} [{result.kind.value}] v{result.version} (tool: {tool_ver}) {status}'
            )


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Porringer extension management (list extension packages)."""
    pass
