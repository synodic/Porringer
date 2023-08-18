"""Click CLI Application"""
import click


class Configuration:
    """Click configuration object"""

    def __init__(self) -> None:
        pass


# Attach our config object to click's hook
pass_config = click.make_pass_decorator(Configuration, ensure=True)


@click.group()
@click.option("-v", "--verbose", count=True, help="Print additional output")
@click.version_option()
@pass_config
def application(config: Configuration) -> None:
    """entry_point group for the CLI commands

    Args:
        config: The CLI configuration object
    """
