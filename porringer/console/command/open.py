"""CLI command implementation for open."""

"""Porringer CLI open command — the OS install-link handler entry point."""

from typing import Annotated

import typer

from porringer.console.command.preview import _PreviewOptions, preview_profile
from porringer.console.common import EXIT_FAILURE
from porringer.console.schema import ConsoleConfiguration
from porringer.core.target import parse_link_target
from porringer.schema import InspectionMode


def open_default(
    context: typer.Context,
    uri: Annotated[str, typer.Argument(help='A porringer:// install link')],
    *,
    as_json: Annotated[
        bool,
        typer.Option('--json', help='Emit machine-readable JSON'),
    ] = False,
) -> None:
    """Open an install link as a read-only preview.

    A link can only show a plan. Nothing is installed or executed; use
    ``porringer install`` to apply a previewed setup.
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    try:
        target = parse_link_target(uri)
    except ValueError as exc:
        configuration.output.error(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc

    if target.profile_url is None:
        configuration.output.error('Install link is missing profile URL')
        raise typer.Exit(EXIT_FAILURE)

    options = _PreviewOptions(inspection_mode=InspectionMode.FAST, as_json=as_json)
    preview_profile(configuration, target.profile_url, options, expected_hash=target.expected_hash)
    configuration.output.print('[muted]Preview only. Run `porringer install <link>` to apply this setup.[/muted]')
