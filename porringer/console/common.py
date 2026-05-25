"""Console and CLI support for common."""

"""Shared helpers and constants for Porringer CLI commands."""

import typer

from porringer.api import API
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import SetupProfile, SyncStrategy

# Process exit codes shared across CLI commands.
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


def create_api(configuration: ConsoleConfiguration) -> API:
    """Create an :class:`API` instance from the CLI configuration."""
    return API(configuration.local_configuration)


async def sniff_profile(api: API, url: str) -> SetupProfile | None:
    """Return the parsed setup profile when *url* points at one.

    Bare ``https`` targets are ambiguous between a setup profile and a
    manifest.  A strict profile parse decides: parse failures mean the URL
    is treated as a manifest, while download failures propagate.

    Returns:
        The parsed profile, or ``None`` when the URL is not a profile.
    """
    try:
        return await api.profile.resolve(url)
    except ValueError as exc:
        if 'Failed to parse profile' in str(exc):
            return None
        raise


def parse_strategy(configuration: ConsoleConfiguration, strategy: str) -> SyncStrategy:
    """Parse a CLI strategy string into a :class:`SyncStrategy`.

    Exits with :data:`EXIT_FAILURE` when *strategy* is not recognized.
    """
    strategy_map = {'minimal': SyncStrategy.MINIMAL, 'latest': SyncStrategy.LATEST, 'exact': SyncStrategy.EXACT}
    sync_strategy = strategy_map.get(strategy.lower())
    if sync_strategy is None:
        configuration.output.error(f"Invalid strategy '{strategy}'. Use: minimal, latest, or exact")
        raise typer.Exit(EXIT_FAILURE)
    return sync_strategy
