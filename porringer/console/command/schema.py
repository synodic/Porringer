"""CLI command implementation for schema."""

"""Porringer CLI schema command module.

Exports the JSON Schema for porringer manifest files.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from porringer.backend.command.sync import SyncCommands
from porringer.console.common import EXIT_FAILURE
from porringer.console.schema import ConsoleConfiguration

app = typer.Typer(help='Export JSON Schema for Porringer manifests')


@app.callback(invoke_without_command=True)
def schema(
    context: typer.Context,
    output: Annotated[
        Path | None,
        typer.Option('--output', '-o', help='Write schema to a file instead of stdout'),
    ] = None,
    indent: Annotated[
        int,
        typer.Option('--indent', help='JSON indentation level'),
    ] = 2,
    url: Annotated[
        bool,
        typer.Option('--url', help='Output the canonical schema URL instead of the schema document'),
    ] = False,
) -> None:
    """Export the JSON Schema for porringer manifest files.

    Prints the schema to stdout by default. Use --output to write to a file.
    Use --url to emit the canonical schema URL instead of the document.

    Examples:
        porringer schema                          # Print schema to stdout
        porringer schema -o schema.json           # Write schema to file
        porringer schema | python -m json.tool    # Pretty-print via pipe
        porringer schema --url                    # Print the canonical schema URL
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    try:
        _emit_schema(configuration, output, indent, url=url)
    except Exception as e:
        configuration.output.error(f'Failed to generate schema: {e}')
        raise typer.Exit(EXIT_FAILURE) from e


def _emit_schema(
    configuration: ConsoleConfiguration,
    output: Path | None,
    indent: int,
    *,
    url: bool = False,
) -> None:
    """Generate the manifest schema and write the document (or its URL) to ``output`` or stdout."""
    schema_dict = SyncCommands.manifest_schema()

    if url:
        content = str(schema_dict['$id'])
        label = 'Schema URL'
    else:
        content = json.dumps(schema_dict, indent=indent)
        label = 'Schema'

    if output is not None:
        output.write_text(content + '\n', encoding='utf-8')
        configuration.output.print(f'{label} written to {output}')
    else:
        # Emit raw content to stdout (not via Rich) so it can be piped
        typer.echo(content)
