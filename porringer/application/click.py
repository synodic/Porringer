"""Click CLI Application"""
import logging
from dataclasses import dataclass
from typing import LiteralString

import click
from synodic_utilities.subprocess import call

from porringer.application.version import is_pipx_installation


@dataclass
class LogLevel:
    """Log level metadata"""

    name: LiteralString
    colour: str


_levels: list[LogLevel] = [
    LogLevel(name="ERROR", colour="red"),
    LogLevel(name="WARNING", colour="yellow"),
    LogLevel(name="INFO", colour="white"),
    LogLevel(name="DEBUG", colour="bright_white"),
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
        level = next(level for level in _levels if level.name == record.levelname)
        click.secho(record, fg=level.colour)


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
        clamped = verbosity >= len(_levels)
        verbosity = min(verbosity, len(_levels) - 1)

        name = _levels[verbosity].name
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

    Args:
        config: _description_

    Raises:
        NotImplementedError: _description_
    """

    if is_pipx_installation():
        call(["pipx", "upgrade", "porringer"], config.logger)
    else:
        raise NotImplementedError()


@self_group.command(name="check")
@pass_config
def self_check(config: Configuration) -> None:
    """Checks for an update

    Args:
        config: _description_

    Raises:
        NotImplementedError: _description_
    """

    if is_pipx_installation():
        call(["pipx", "upgrade", "porringer"], config.logger)
    else:
        raise NotImplementedError()
