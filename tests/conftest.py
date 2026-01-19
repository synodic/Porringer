"""Shared pytest configuration and fixtures."""

import pytest
from rich.console import Console

from porringer.console.schema import Configuration


@pytest.fixture
def test_config() -> Configuration:
    """Configuration for CLI testing."""
    console = Console(no_color=True, force_terminal=False)
    return Configuration(console=console)
