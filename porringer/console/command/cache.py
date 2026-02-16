"""Porringer CLI cache command module for managing manifest directories."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from porringer.api import API
from porringer.console.schema import Configuration

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
    configuration = context.ensure_object(Configuration)
    api = API(configuration.local_configuration)

    try:
        directory = api.cache.add_directory(path, name=name)
        display_name = directory.name or str(directory.path)
        configuration.console.print(f'[green]Added:[/green] {display_name}')
    except ValueError as e:
        configuration.console.print(f'[red]Error:[/red] {e}')
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
    configuration = context.ensure_object(Configuration)
    api = API(configuration.local_configuration)

    if api.cache.remove_directory(path):
        configuration.console.print(f'[green]Removed:[/green] {path}')
    else:
        configuration.console.print(f'[yellow]Not found:[/yellow] {path}')
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
    configuration = context.ensure_object(Configuration)
    api = API(configuration.local_configuration)

    directories = api.cache.list_directories()

    if not directories:
        configuration.console.print('[yellow]No cached directories[/yellow]')
        return

    table = Table(title='Cached Directories', show_header=True, header_style='bold magenta')
    table.add_column('Path', style='cyan')
    table.add_column('Name', style='white')
    if validate:
        table.add_column('Status', style='white')
        table.add_column('Manifest', style='white')

        validations = api.cache.validate_directories(check_manifest=True)
        for v in validations:
            name = v.directory.name or ''
            status = '[green]OK[/green]' if v.exists else '[red]Missing[/red]'
            manifest = '[green]Found[/green]' if v.has_manifest else '[red]Missing[/red]' if v.exists else '-'
            table.add_row(str(v.directory.path), name, status, manifest)
    else:
        for directory in directories:
            name = directory.name or ''
            table.add_row(str(directory.path), name)

    configuration.console.print(table)


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
    configuration = context.ensure_object(Configuration)
    api = API(configuration.local_configuration)

    if not yes and not typer.confirm('Clear all cached directories?', default=False):
        configuration.console.print('[yellow]Aborted[/yellow]')
        raise typer.Exit(0)

    api.cache.clear()
    configuration.console.print('[green]Cache cleared[/green]')


@app.callback(invoke_without_command=True, no_args_is_help=True)
def cache_default(context: typer.Context) -> None:
    """Manage cached manifest directories.

    Use 'porringer cache add' to register directories and
    'porringer cache list' to view them.
    """
    pass
