"""Click CLI Application"""
import click


class Configuration:
    """Click configuration object"""

    def __init__(self) -> None:
        pass


# Attach our config object to click's hook
pass_config = click.make_pass_decorator(Configuration, ensure=True)


@click.group()
@click.option("-v", "--verbose", count=True, default=0, help="Print additional output")
@click.version_option()
@pass_config
def application(config: Configuration, verbose: int) -> None:
    """entry_point group for the CLI commands

    Args:
        config: The CLI configuration object
    """


@application.group(invoke_without_command=True)
@pass_config
def self(config: Configuration) -> None:
    """Command group to inspect Porringer itself

    Args:
        config: The CLI configuration object
    """
