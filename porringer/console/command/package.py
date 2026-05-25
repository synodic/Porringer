"""CLI command implementation for package."""

"""Porringer CLI package command module for managed-package operations."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from porringer.backend.command.package import PackageCommands
from porringer.console.schema import ConsoleConfiguration
from porringer.core.schema import PackageRef
from porringer.schema import SetupActionResult
from porringer.utility.exception import PluginError

app = typer.Typer()


def _print_result(configuration: ConsoleConfiguration, result: SetupActionResult, fallback_verb: str) -> None:
    """Print a success/failure message and exit on failure."""
    if result.success:
        configuration.output.success(result.message or result.action.description)
    else:
        configuration.output.error(result.message or f'{fallback_verb} failed', prefix=None)
        raise typer.Exit(code=1)


@app.command('list')
def package_list(
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
        packages = asyncio.run(PackageCommands.list(plugin_name, resolved_path))
    except PluginError as e:
        configuration.output.error(str(e.error))
        raise typer.Exit(code=1) from None

    if not packages:
        configuration.output.warning(f'No packages found for plugin: {plugin_name}')
    else:
        table = Table(title=f'Packages ({plugin_name})')
        table.add_column('Name', style='cyan')
        table.add_column('Version', style='green')
        table.add_column('Host', style='magenta')

        for pkg in sorted(packages, key=lambda p: p.name.lower()):
            host_text = ''
            if pkg.relation is not None:
                host_text = f'{pkg.relation.host} ({pkg.relation.kind.value})'
            table.add_row(pkg.name, pkg.version or 'n/a', host_text)

        configuration.output.print(table)


@app.command('install')
def package_install(
    context: typer.Context,
    plugin_name: Annotated[str, typer.Argument(help='Plugin name (e.g. pipx, uv, npm)')],
    package_name: Annotated[str, typer.Argument(help='Package to install')],
    runtime_tag: Annotated[str | None, typer.Option('--runtime', '-r', help='Runtime tag (e.g. 3.12)')] = None,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Install a package if it is not already present."""
    configuration = context.ensure_object(ConsoleConfiguration)
    package = PackageRef(name=package_name)

    result = asyncio.run(PackageCommands.install(plugin_name, package, runtime_tag=runtime_tag, dry_run=dry_run))
    _print_result(configuration, result, 'Install')


@app.command('upgrade')
def package_upgrade(
    context: typer.Context,
    plugin_name: Annotated[str, typer.Argument(help='Plugin name (e.g. pipx, uv, npm)')],
    package_name: Annotated[str, typer.Argument(help='Package to upgrade')],
    runtime_tag: Annotated[str | None, typer.Option('--runtime', '-r', help='Runtime tag (e.g. 3.12)')] = None,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Upgrade a package to its latest version (or install if absent)."""
    configuration = context.ensure_object(ConsoleConfiguration)
    package = PackageRef(name=package_name)

    result = asyncio.run(PackageCommands.upgrade(plugin_name, package, runtime_tag=runtime_tag, dry_run=dry_run))
    _print_result(configuration, result, 'Upgrade')


@app.command('uninstall')
def package_uninstall(
    context: typer.Context,
    plugin_name: Annotated[str, typer.Argument(help='Plugin name (e.g. pipx, uv, npm)')],
    package_name: Annotated[str, typer.Argument(help='Package to uninstall')],
    runtime_tag: Annotated[str | None, typer.Option('--runtime', '-r', help='Runtime tag (e.g. 3.12)')] = None,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show what would be done without executing')] = False,
) -> None:
    """Uninstall a globally-installed package."""
    configuration = context.ensure_object(ConsoleConfiguration)
    package = PackageRef(name=package_name)

    result = asyncio.run(PackageCommands.uninstall(plugin_name, package, runtime_tag=runtime_tag, dry_run=dry_run))
    _print_result(configuration, result, 'Uninstall')


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Managed-package operations (list, install, upgrade, uninstall)."""
    pass
