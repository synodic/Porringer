"""Typer CLI Application"""

import logging
from typing import Annotated

import typer
from rich.console import Console

from porringer.console.command.plugin import app as plugin_app
from porringer.console.command.self import app as self_app
from porringer.console.schema import LOG_LEVELS, MAX_VERBOSITY_LEVEL, Configuration

# TODO: Hook up version to the version in pyproject.toml
__version__ = '0.1.0'

app = typer.Typer()
app.add_typer(plugin_app, name='plugin')
app.add_typer(self_app, name='self')


class TyperHandler(logging.Handler):
    """A logging handler that outputs to typer"""

    def __init__(self, console: Console) -> None:
        """Initializes the handler"""
        logging.Handler.__init__(self)

        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        """Emits the log record to typer"""
        level = next(level for level in LOG_LEVELS if level.name == record.levelname)
        self.console.print(record, style=level.colour)


def version_callback(value: bool) -> None:
    """Callback for the version option"""
    if value:
        print(f'Awesome CLI Version: {__version__}')
        raise typer.Exit()


@app.callback(invoke_without_command=True, no_args_is_help=True)
def application(
    context: typer.Context,
    verbose: Annotated[int, typer.Option('--verbose', '-v', count=True, help='', min=0, max=MAX_VERBOSITY_LEVEL)] = 0,
    debug: Annotated[bool, typer.Option('--debug', help='')] = False,
    version: Annotated[
        bool | None,
        typer.Option('--version', callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """A tool for automatic facilitation of program updates and package managers.

    Args:
        context: The click context object
        verbose: The input verbosity level
        debug: The debug flag
        version: The version request
    """
    configuration = context.ensure_object(Configuration)

    configuration.debug = debug
    configuration.verbosity = verbose

    logger = logging.getLogger('porringer')
    handler = TyperHandler(configuration.console)
    logger.addHandler(handler)
