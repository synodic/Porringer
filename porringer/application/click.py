"""Click CLI Application"""
import logging
from typing import LiteralString, TypedDict

import click
from synodic_utilities.subprocess import call

from porringer.application.version import is_pipx_installation


class LogLevels(TypedDict):
    """Log level metadata"""

    name: LiteralString
    colour: str


__levels: list[LogLevels] = [
    LogLevels(name="ERROR", colour="red"),
    LogLevels(name="WARNING", colour="yellow"),
    LogLevels(name="INFO", colour="white"),
    LogLevels(name="DEBUG", colour="orange"),
]


class ClickHandler(logging.Handler):
    """_summary_"""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        """_summary_

        Args:
            record: _description_
        """

        click.secho(record, fg=__levels[record.levelname])


class Configuration:
    """The configuration data available to the CLI application"""

    def __init__(self) -> None:
        self.debug = False

        self.logger = logging.getLogger("porringer")
        handler = ClickHandler()
        self.logger.addHandler(handler)

    def set_logger_level(self, verbosity: int) -> None:
        """_summary_

        Args:
            verbosity: _description_
        """
        clamped = verbosity >= len(__levels)
        verbosity = min(verbosity, len(__levels) - 1)

        name = __levels[verbosity].name
        self.logger.setLevel(name)

        if clamped:
            self.logger.debug("The debug level was clamped to %s", name)

        self.logger.info("Logging set to %s", name)


# Attach our config object to click's hook
pass_config = click.make_pass_decorator(Configuration, ensure=True)


@click.group(invoke_without_command=True)
@click.option("-v", "--verbose", count=True, help="Print additional output")
@click.option("--debug", is_flag=True, help="Enables additional debug information")
@click.version_option()
@pass_config
def application(config: Configuration, verbose: int, debug: bool) -> None:
    """entry_point group for the CLI commands

    Args:
        config: _description_
        verbose: _description_
        debug: _description_
    """
    config.debug = debug

    config.set_logger_level(verbose)


@application.group(name="self", invoke_without_command=True)
def self_group() -> None:
    """Command group to inspect Porringer itself"""


@self_group.command(name="update")
@pass_config
def self_update(config: Configuration) -> None:
    """Updates

    Raises:
        NotImplementedError: Not implemented
    Args:
        config: _description_
    """

    if is_pipx_installation():
        call(["pipx", "upgrade", "porringer"], config.logger)
    else:
        raise NotImplementedError()


@self_group.command(name="check")
@pass_config
def self_check(config: Configuration) -> None:
    """Checks for an update

    Raises:
        NotImplementedError: Not implemented
    Args:
        config: _description_
    """
