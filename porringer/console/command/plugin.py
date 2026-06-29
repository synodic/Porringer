"""CLI command implementation for plugin.

Porringer CLI plugin command module.
"""

import asyncio
from typing import Annotated

import typer

from porringer.backend.command.plugin import PluginCommands
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import PluginOperationResult
from porringer.utility.exception import PluginError

app = typer.Typer()


def _report_results(configuration: ConsoleConfiguration, results: list[PluginOperationResult]) -> None:
    """Print batch operation results and exit non-zero on any failure.

    Args:
        configuration: The console configuration providing output.
        results: Per-plugin operation results with ``success`` and
            ``message`` attributes.
    """
    has_failure = False
    for result in results:
        if result.success:
            configuration.output.success(result.message)
        else:
            configuration.output.error(result.message, prefix=None)
            has_failure = True

    if has_failure:
        raise typer.Exit(code=1)


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


@app.command('install')
def plugin_install(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to install (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Install plugins from PyPI."""
    configuration = context.ensure_object(ConsoleConfiguration)

    for plugin in plugins:
        try:
            result = asyncio.run(PluginCommands.install(plugin, dry_run=dry_run))
        except PluginError as e:
            configuration.output.error(e.error)
            raise typer.Exit(code=1) from None

        if result.success:
            configuration.output.success(result.message)
        else:
            configuration.output.error(result.message, prefix=None)
            raise typer.Exit(code=1)


@app.command('upgrade')
def plugin_upgrade(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to upgrade (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Upgrade installed plugins."""
    configuration = context.ensure_object(ConsoleConfiguration)

    results = asyncio.run(PluginCommands.upgrade(plugins, dry_run=dry_run))

    _report_results(configuration, results)


@app.command('uninstall')
def plugin_uninstall(
    context: typer.Context,
    plugins: Annotated[list[str], typer.Argument(help='Plugins to remove (PyPI package names)')],
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Uninstall installed plugins."""
    configuration = context.ensure_object(ConsoleConfiguration)

    results = asyncio.run(PluginCommands.uninstall(plugins, dry_run=dry_run))

    _report_results(configuration, results)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Porringer extension management (install, upgrade, uninstall extension packages)."""
    pass
