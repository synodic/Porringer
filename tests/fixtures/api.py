"""Helpers for api."""

"""Session-scoped API and configuration fixtures.

Provides a single ``API`` instance (``session_api``) backed by a
session-scoped temporary directory tree.  Tests that only *read*
from the API (``parse_manifest``, ``sync.inspect``, ``plugin.list``)
should use ``session_api`` instead of the function-scoped ``test_api``.
"""

from pathlib import Path

import pytest

from porringer.api import API
from porringer.backend.schema import GlobalConfiguration
from porringer.schema import LocalConfiguration


@pytest.fixture(scope='session')
def session_temp_dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, Path]:
    """Create the directory tree once for the entire session.

    Returns:
        ``(root, data_dir, config_dir, cache_dir)``
    """
    root = tmp_path_factory.mktemp('session_dirs')
    data_dir = root / 'data'
    data_dir.mkdir()
    config_dir = root / 'config'
    config_dir.mkdir()
    cache_dir = root / 'cache'
    cache_dir.mkdir()
    return root, data_dir, config_dir, cache_dir


@pytest.fixture(scope='session')
def session_global_configuration(session_temp_dirs: tuple[Path, Path, Path, Path]) -> GlobalConfiguration:
    """Session-scoped ``GlobalConfiguration``."""
    _, data_dir, config_dir, _ = session_temp_dirs
    return GlobalConfiguration(config_directory=config_dir, data_directory=data_dir)


@pytest.fixture(scope='session')
def session_local_configuration(session_temp_dirs: tuple[Path, Path, Path, Path]) -> LocalConfiguration:
    """Session-scoped ``LocalConfiguration``."""
    _, _, _, cache_dir = session_temp_dirs
    return LocalConfiguration(cache_directory=cache_dir)


@pytest.fixture(scope='session')
def session_api(
    session_local_configuration: LocalConfiguration,
    session_global_configuration: GlobalConfiguration,
) -> API:
    """Shared read-only ``API`` instance for the full test session.

    Use this fixture in tests that do not mutate API state (e.g.
    ``parse_manifest``, ``sync.inspect``, ``plugin.list``).
    """
    return API(session_local_configuration, global_configuration=session_global_configuration)
