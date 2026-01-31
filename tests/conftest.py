"""Shared pytest configuration and fixtures."""

import tempfile
from logging import Logger
from pathlib import Path

import pytest
from rich.console import Console

from porringer.backend.cache import DirectoryCacheManager
from porringer.console.schema import Configuration


@pytest.fixture
def test_config() -> Configuration:
    """Configuration for CLI testing."""
    console = Console(no_color=True, force_terminal=False)
    return Configuration(console=console)


@pytest.fixture
def test_logger() -> Logger:
    """Logger for testing."""
    return Logger('test')


@pytest.fixture
def temp_cache_dir():
    """Temporary directory structure for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        yield tmp_path, data_dir


@pytest.fixture
def cache_manager(test_logger, temp_cache_dir):
    """DirectoryCacheManager instance for testing."""
    _, data_dir = temp_cache_dir
    return DirectoryCacheManager(data_dir, test_logger)
