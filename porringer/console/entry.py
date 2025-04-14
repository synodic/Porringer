"""Typer CLI Application"""

import logging
from dataclasses import dataclass
from typing import LiteralString

import click

from porringer.api import API
from porringer.schema import ListPluginsParameters, LocalConfiguration, Parameters


@dataclass
class LogLevel:
    """Log level metadata"""

    name: LiteralString
    colour: str


_levels: list[LogLevel] = [
    LogLevel(name='ERROR', colour='red'),
    LogLevel(name='WARNING', colour='yellow'),
    LogLevel(name='INFO', colour='white'),
    LogLevel(name='DEBUG', colour='bright_white'),
]


class TyperHandler(logging.Handler):
    """A logging handler that outputs to type"""

    def __init__(self) -> None:
        """Initializes the handler"""
        logging.Handler.__init__(self)

    @staticmethod
    def emit(record: logging.LogRecord) -> None:
        """Emits the log record to typer"""
        level = next(level for level in _levels if level.name == record.levelname)
        click.secho(record, fg=level.colour)


class Configuration:
    """The configuration data available to the CLI application"""

    def __init__(self) -> None:
        """Initializes the configuration"""
        self.debug = False

        self.logger = logging.getLogger('porringer')
        handler = TyperHandler()
        self.logger.addHandler(handler)

        configuration = LocalConfiguration()
        parameters = Parameters(self.logger)

        self.api = API(configuration, parameters)

    def set_logger_level(self, verbosity: int) -> None:
        """Set the logger level based on the verbosity

        Args:
            verbosity: The verbosity level
        """
        clamped = verbosity >= len(_levels)
        verbosity = min(verbosity, len(_levels) - 1)

        name = _levels[verbosity].name
        self.logger.setLevel(name)

        if clamped:
            self.logger.debug('The debug level was clamped to %s', name)

        self.logger.info('Logging set to %s', name)

    def set_debug(self, debug: bool) -> None:
        """Set the configuration debug state

        Args:
            debug: The value to set the debug state
        """
        self.debug = debug


# Attach our config object to click's hook
pass_config = click.make_pass_decorator(Configuration, ensure=True)


@click.group(invoke_without_command=True, no_args_is_help=True)
@click.option('-v', '--verbose', count=True, help='Print additional output')
@click.option('--debug', is_flag=True, help='Enables additional debug information')
@click.version_option()
@pass_config
def application(config: Configuration, verbose: int, debug: bool) -> None:
    """A tool for automatic and facilitating generic program updates

    Args:
        config: The click configuration object
        verbose: The input verbosity level
        debug: The debug flag
    """
    config.debug = debug

    config.set_logger_level(verbose)


@application.group(name='self', invoke_without_command=True)
def self_group() -> None:
    """Command group to inspect Porringer itself"""


@self_group.command(name='update')
def self_update() -> None:
    """Updates the Porringer application.

    Raises:
        NotImplementedError: This functionality is not yet implemented.
    """


@self_group.command(name='check')
def self_check() -> None:
    """Checks for updates to the Porringer application.

    Raises:
        NotImplementedError: This functionality is not yet implemented.
    """


@application.group(name='plugin', invoke_without_command=True)
def plugin_group() -> None:
    """Command group to inspect Porringer plugins"""


@plugin_group.command(name='list')
@pass_config
def plugin_list(config: Configuration) -> None:
    """Lists available plugins

    Args:
        config: The click configuration object
    """
    parameters = ListPluginsParameters()
    results = config.api.list_plugins(parameters)

    for result in results:
        click.echo(result)
