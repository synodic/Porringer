"""Porringer CLI download command module for downloading files."""

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import (
    APIParameters,
    DownloadParameters,
    ProgressCallback,
)

app = typer.Typer()


def _create_api(configuration: Configuration) -> API:
    """Create and return API instance.

    Args:
        configuration: CLI configuration.

    Returns:
        Initialized API instance.
    """
    api_parameters = APIParameters(logging.getLogger('porringer'))
    return API(configuration.local_configuration, api_parameters)


def _create_progress_callback(configuration: Configuration) -> ProgressCallback | None:
    """Create a progress callback for downloads.

    Args:
        configuration: CLI configuration.

    Returns:
        Progress callback function or None.
    """

    def progress_callback(downloaded: int, total: int | None) -> None:
        if total:
            percent = (downloaded / total) * 100
            configuration.console.print(f'\r[dim]Downloading: {percent:.1f}%[/dim]', end='')
        else:
            mb = downloaded / (1024 * 1024)
            configuration.console.print(f'\r[dim]Downloaded: {mb:.2f} MB[/dim]', end='')

    return progress_callback


@app.callback(invoke_without_command=True)
def download_default(
    context: typer.Context,
    url: Annotated[str, typer.Argument(help='URL to download')],
    destination: Annotated[Path, typer.Argument(help='Destination file path')],
    *,
    expected_hash: Annotated[
        str | None, typer.Option('--hash', '-H', help='Expected hash in algorithm:hexdigest format')
    ] = None,
    expected_size: Annotated[int | None, typer.Option('--size', '-S', help='Expected file size in bytes')] = None,
    timeout: Annotated[int, typer.Option('--timeout', '-t', help='Download timeout in seconds')] = 300,
) -> None:
    """Download a file with optional hash verification.

    Downloads a file from a URL to the specified destination. Optionally verifies
    the file hash and size after download.

    Examples:
        porringer download https://example.com/file.zip ./file.zip
        porringer download https://example.com/file.zip ./file.zip --hash sha256:abc123...
    """
    configuration = context.ensure_object(Configuration)

    api = _create_api(configuration)

    params = DownloadParameters(
        url=url,
        destination=destination,
        expected_hash=expected_hash,
        expected_size=expected_size,
        timeout=timeout,
    )

    result = api.update.download(params, _create_progress_callback(configuration))
    configuration.console.print()  # Newline after progress

    if result.success:
        verified_msg = ' (hash verified)' if result.verified else ''
        size_mb = result.size / (1024 * 1024)
        configuration.console.print(
            Panel(
                f'[green]Download complete![/green]{verified_msg}\n\n'
                f'[bold]File:[/bold] {result.path}\n'
                f'[bold]Size:[/bold] {size_mb:.2f} MB',
                border_style='green',
            )
        )
    else:
        configuration.console.print(f'[red]Error:[/red] {result.message}')
        raise typer.Exit(1)
