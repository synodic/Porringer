"""Porringer CLI update command module"""

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel

from porringer.api import API
from porringer.console.schema import Configuration
from porringer.schema import (
    APIParameters,
    CheckUpdateParameters,
    DownloadParameters,
    ProgressCallback,
    UpdateSource,
)
from porringer.utility.exception import UpdateError

app = typer.Typer()

# Source mapping
SOURCE_MAP = {
    'github': UpdateSource.GITHUB_RELEASES,
    'pypi': UpdateSource.PYPI,
    'custom': UpdateSource.CUSTOM_URL,
}


def _parse_update_source(source: str, configuration: Configuration) -> UpdateSource | None:
    """Parse and validate update source string.

    Args:
        source: Source string (github, pypi, or custom).
        configuration: CLI configuration.

    Returns:
        UpdateSource enum value or None if invalid.
    """
    if source.lower() not in SOURCE_MAP:
        configuration.console.print(f"[red]Error:[/red] Unknown source '{source}'. Use: github, pypi, or custom")
        return None
    return SOURCE_MAP[source.lower()]


def _create_api(configuration: Configuration) -> API:
    """Create and return API instance.

    Args:
        configuration: CLI configuration.

    Returns:
        Initialized API instance.
    """
    api_parameters = APIParameters(logging.getLogger('porringer'))
    return API(configuration.local_configuration, api_parameters)


@app.command('check')
def update_check(
    context: typer.Context,
    source: Annotated[str, typer.Option('--source', '-s')] = 'github',
    current_version: Annotated[str, typer.Option('--current', '-c')] = '0.0.0',
    repo: Annotated[str | None, typer.Option('--repo', '-r')] = None,
    package: Annotated[str | None, typer.Option('--package', '-p')] = None,
    url: Annotated[str | None, typer.Option('--url', '-u')] = None,
    github_token: Annotated[str | None, typer.Option('--github-token', envvar='GITHUB_TOKEN')] = None,
    include_prereleases: Annotated[bool, typer.Option('--prereleases')] = False,
) -> None:
    """Check for available updates from a source.

    Examples:
        porringer update check --source github --repo owner/repo --current 1.0.0
        porringer update check --source pypi --package requests --current 2.28.0
        porringer update check --source custom --url https://example.com/version.json --current 1.0.0
    """
    configuration = context.ensure_object(Configuration)

    update_source = _parse_update_source(source, configuration)
    if not update_source:
        raise typer.Exit(1)

    api = _create_api(configuration)

    params = CheckUpdateParameters(
        source=update_source,
        current_version=current_version,
        repo=repo,
        package=package,
        url=url,
        github_token=github_token,
        include_prereleases=include_prereleases,
    )

    try:
        result = api.update.check(params)
    except UpdateError as e:
        configuration.console.print(f'[red]Error:[/red] {e.error}')
        raise typer.Exit(1) from e

    configuration.console.print(f'\n[bold]Current version:[/bold] {result.current_version}')

    if result.available:
        configuration.console.print(
            Panel(
                f'[green]Update available![/green]\n\n'
                f'[bold]Latest version:[/bold] {result.latest_version}\n'
                f'[bold]Download:[/bold] {result.download_url or "N/A"}\n'
                f'[bold]Release notes:[/bold] {result.release_notes_url or "N/A"}\n'
                f'[bold]Published:[/bold] {result.published_at or "N/A"}',
                border_style='green',
            )
        )
    else:
        configuration.console.print(Panel('[dim]You are up to date.[/dim]', border_style='dim'))


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


@app.command('download')
def update_download(
    context: typer.Context,
    url: Annotated[str, typer.Argument(help='URL to download')],
    destination: Annotated[Path, typer.Argument(help='Destination file path')],
    expected_hash: Annotated[str | None, typer.Option('--hash', '-H')] = None,
    expected_size: Annotated[int | None, typer.Option('--size', '-S')] = None,
    timeout: Annotated[int, typer.Option('--timeout', '-t')] = 300,
) -> None:
    """Download a file with optional hash verification.

    Examples:
        porringer update download https://example.com/file.zip ./file.zip
        porringer update download https://example.com/file.zip ./file.zip --hash sha256:abc123...
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


@app.callback(invoke_without_command=True, no_args_is_help=True)
def update_default() -> None:
    """Check for updates and download files."""
    pass
