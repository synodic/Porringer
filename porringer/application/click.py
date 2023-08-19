"""Click CLI Application"""
import click
from pydantic import BaseModel, Field


class Configuration(BaseModel):
    """The configuration data available to the CLI application"""

    debug: bool = Field(default=False, description="")
    verbosity: int = Field(default=0, description="")


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
    config.verbosity = verbose
    config.debug = debug


@application.group(name="self", invoke_without_command=True)
@pass_config
def self_group(config: Configuration) -> None:
    """Command group to inspect Porringer itself

    Args:
        config: _description_
    """
    assert not config.debug
