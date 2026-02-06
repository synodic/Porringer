"""Shared pytest configuration and fixtures."""

import tempfile
from pathlib import Path

import pytest
from rich.console import Console

from porringer.api import API
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.schema import GlobalConfiguration
from porringer.console.schema import Configuration
from porringer.schema import LocalConfiguration


@pytest.fixture
def test_config() -> Configuration:
    """Configuration for CLI testing."""
    console = Console(no_color=True, force_terminal=False)
    return Configuration(console=console)


@pytest.fixture
def temp_cache_dir():
    """Temporary directory structure for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        yield tmp_path, data_dir


@pytest.fixture
def cache_manager(temp_cache_dir):
    """DirectoryCacheManager instance for testing."""
    _, data_dir = temp_cache_dir
    return DirectoryCacheManager(data_dir)


@pytest.fixture
def test_global_configuration(temp_cache_dir):
    """GlobalConfiguration with isolated temporary directories.

    This ensures tests don't modify the system's porringer cache.
    """
    tmp_path, data_dir = temp_cache_dir
    config_dir = tmp_path / 'config'
    config_dir.mkdir(exist_ok=True)
    return GlobalConfiguration(config_directory=config_dir, data_directory=data_dir)


@pytest.fixture
def test_local_configuration(temp_cache_dir):
    """LocalConfiguration with isolated temporary cache directory."""
    tmp_path, _ = temp_cache_dir
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir(exist_ok=True)
    return LocalConfiguration(cache_directory=cache_dir)


@pytest.fixture
def test_api(test_local_configuration, test_global_configuration):
    """API instance with isolated temporary directories.

    This ensures tests don't modify the system's porringer cache.
    """
    return API(test_local_configuration, global_configuration=test_global_configuration)
