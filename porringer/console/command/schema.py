"""Porringer CLI schema command module.

Exports the JSON Schema for porringer manifest files.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from porringer.backend.command.sync import SyncCommands
from porringer.console.schema import ConsoleConfiguration

app = typer.Typer(help='Export JSON Schema for Porringer manifests')

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


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
) -> None:
    """Export the JSON Schema for porringer manifest files.

    Prints the schema to stdout by default. Use --output to write to a file.

    Examples:
        porringer schema                          # Print to stdout
        porringer schema -o schema.json           # Write to file
        porringer schema | python -m json.tool    # Pretty-print via pipe
    """
    configuration = context.ensure_object(ConsoleConfiguration)

    try:
        schema_dict = SyncCommands.manifest_schema()
        schema_json = json.dumps(schema_dict, indent=indent)

        if output is not None:
            output.write_text(schema_json + '\n', encoding='utf-8')
            configuration.console.print(f'Schema written to {output}')
        else:
            # Print raw JSON to stdout (not via Rich) so it can be piped
            print(schema_json)

    except Exception as e:
        configuration.console.print(f'[red]Error:[/red] Failed to generate schema: {e}')
        raise typer.Exit(EXIT_FAILURE) from e
