"""CLI command implementation for cache.

Porringer CLI cache command module for managing manifest directories.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from porringer.api import API
from porringer.console.schema import ConsoleConfiguration

app = typer.Typer(help='Manage cached manifest directories')


@app.command('add')
def cache_add(
    context: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            help='Path to directory containing manifest files',
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option('--name', '-n', help='Optional display name for the directory'),
    ] = None,
) -> None:
    """Add a directory to the cache.

    The directory will be stored and can be used for batch setup operations.

    Args:
        context: The typer context.
        path: Path to directory.
        name: Optional display name.
    """
    configuration = context.ensure_object(ConsoleConfiguration)
    api = API(configuration.local_configuration)

    try:
        status = asyncio.run(api.project.add(path, name=name))
        directory = status.directory
        display_name = directory.name or str(directory.path)
        configuration.output.success(display_name, prefix='Added')
    except ValueError as e:
        configuration.output.error(str(e))
        raise typer.Exit(1) from e


@app.command('remove')
def cache_remove(
    context: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            help='Path to directory to remove from cache',
            resolve_path=True,
        ),
    ],
) -> None:
    """Remove a directory from the cache.

    Args:
        context: The typer context.
        path: Path to remove.
    """
    configuration = context.ensure_object(ConsoleConfiguration)
    api = API(configuration.local_configuration)

    if asyncio.run(api.project.remove(path)):
        configuration.output.success(str(path), prefix='Removed')
    else:
        configuration.output.warning(str(path), prefix='Not found')
        raise typer.Exit(1)


@app.command('list')
def cache_list(
    context: typer.Context,
    validate: Annotated[
        bool,
        typer.Option('--validate', '-v', help='Check if directories exist'),
    ] = False,
) -> None:
    """List cached directories.

    Args:
        context: The typer context.
        validate: Check if paths exist.
    """
    configuration = context.ensure_object(ConsoleConfiguration)
    api = API(configuration.local_configuration)

    directories = asyncio.run(api.project.list(validate=validate, check_manifest=validate))

    if not directories:
        configuration.output.warning('No cached directories')
        return

    table = Table(title='Cached Directories', show_header=True, header_style='bold magenta')
    table.add_column('Path', style='cyan')
    table.add_column('Name', style='white')
    if validate:
        table.add_column('Status', style='white')
        table.add_column('Manifest', style='white')

        for v in directories:
            name = v.name or ''
            status = '[green]OK[/green]' if v.exists else '[red]Missing[/red]'
            manifest = '[green]Found[/green]' if v.has_manifest else '[red]Missing[/red]' if v.exists else '-'
            table.add_row(str(v.path), name, status, manifest)
    else:
        for v in directories:
            name = v.name or ''
            table.add_row(str(v.path), name)

    configuration.output.print(table)


@app.command('clear')
def cache_clear(
    context: typer.Context,
    yes: Annotated[
        bool,
        typer.Option('--yes', '-y', help='Skip confirmation'),
    ] = False,
) -> None:
    """Clear all cached directories.

    Args:
        context: The typer context.
        yes: Skip confirmation.
    """
    configuration = context.ensure_object(ConsoleConfiguration)
    api = API(configuration.local_configuration)

    if not yes and not typer.confirm('Clear all cached directories?', default=False):
        configuration.output.warning('Aborted')
        raise typer.Exit(0)

    asyncio.run(api.project.clear())
    configuration.output.success('Cache cleared')


@app.callback(invoke_without_command=True, no_args_is_help=True)
def cache_default(context: typer.Context) -> None:
    """Manage cached manifest directories.

    Use 'porringer cache add' to register directories and
    'porringer cache list' to view them.
    """
    pass
