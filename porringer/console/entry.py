"""Typer CLI Application"""

import logging
from importlib.metadata import version
from typing import Annotated

import typer
from rich.console import Console

from porringer.console.command.cache import app as cache_app
from porringer.console.command.check import app as check_app
from porringer.console.command.download import app as download_app
from porringer.console.command.package import app as package_app
from porringer.console.command.plugin import app as plugin_app
from porringer.console.command.schema import app as schema_app
from porringer.console.command.self import app as self_app
from porringer.console.command.sync import app as sync_app
from porringer.console.schema import LOG_LEVELS, MAX_VERBOSITY_LEVEL, VERBOSITY_DEBUG_THRESHOLD, ConsoleConfiguration

__version__ = version('porringer')

app = typer.Typer()
app.add_typer(cache_app, name='cache')
app.add_typer(check_app, name='check')
app.add_typer(download_app, name='download')
app.add_typer(package_app, name='package')
app.add_typer(schema_app, name='schema')
app.add_typer(sync_app, name='sync')
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
        message = self.format(record)
        self.console.print(message, style=level.colour)


def version_callback(value: bool) -> None:
    """Callback for the version option"""
    if value:
        print(f'Porringer {__version__}')
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
    configuration = context.ensure_object(ConsoleConfiguration)

    configuration.debug = debug
    configuration.verbosity = verbose

    logger = logging.getLogger('porringer')

    # Remove any previously-attached TyperHandlers so that repeated
    # CLI invocations (e.g. in tests) don't accumulate handlers
    # pointing to closed output streams.
    logger.handlers = [h for h in logger.handlers if not isinstance(h, TyperHandler)]

    handler = TyperHandler(configuration.console)
    logger.addHandler(handler)

    # The porringer logger has its own handler; disable propagation so
    # records are not also handled by the root logger.  This avoids
    # duplicate output and, under test runners that temporarily swap
    # sys.stdout / sys.stderr (e.g. pytest log_cli + CliRunner),
    # prevents stream wrappers from being garbage-collected during
    # capture suspension (Python 3.14 raises on BytesIO.getvalue()
    # after close).
    logger.propagate = False

    if debug or verbose >= VERBOSITY_DEBUG_THRESHOLD:
        logger.setLevel(logging.DEBUG)
    elif verbose >= 1:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)
