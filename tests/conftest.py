"""Shared pytest configuration and fixtures."""

import pytest
from rich.console import Console

from porringer.console.schema import Configuration


@pytest.fixture
def test_config() -> Configuration:
    """Configuration for CLI testing with no color output."""
    console = Console(no_color=True)
    return Configuration(console=console)
