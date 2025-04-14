"""Porringer CLI self command module."""

import typer

app = typer.Typer()


@app.command('update')
def self_update() -> None:
    """Updates the Porringer application.

    Raises:
        NotImplementedError: This functionality is not yet implemented.
    """


@app.command('check')
def self_check() -> None:
    """Checks for updates to the Porringer application.

    Raises:
        NotImplementedError: This functionality is not yet implemented.
    """


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application() -> None:
    """Management of the Porringer instance running the CLI application."""
    pass
