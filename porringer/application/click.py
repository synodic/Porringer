"""Click CLI Application"""
import logging

import click
from synodic_utilities.subprocess import call

from porringer.application.version import is_pipx_installation


class Configuration:
    """The configuration data available to the CLI application"""

    debug: bool
    logger: logging.Logger


# Attach our config object to click's hook
pass_config = click.make_pass_decorator(Configuration, ensure=True)


@click.group(invoke_without_command=True)
@click.option("-v", "--verbose", count=True, help="Print additional output")
@click.option("--debug/--no-debug", help="Enables additional debug information")
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

    config.logger = logging.getLogger("porringer")

    handler = logging.StreamHandler()
    handler.setLevel(verbose)

    # Add handler to the logger
    config.logger.addHandler(handler)

    # TODO: Run a self update check


@application.group(name="self", invoke_without_command=True)
def self_group() -> None:
    """Command group to inspect Porringer itself"""


@self_group.command(name="update")
@pass_config
def self_update(config: Configuration) -> None:
    """_summary_

    Raises:
        NotImplementedError: Not implemented
    Args:
        config: _description_
    """

    if is_pipx_installation():
        call(["pipx", "upgrade", "porringer"], config.logger)
    else:
        raise NotImplementedError()
