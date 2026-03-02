"""Session-scoped cached package list and plugin discovery for the ``mock_packages`` marker.

Runs ``pip list --format=json`` **once** at session start and pre-discovers
all plugins once.  Tests decorated with ``@pytest.mark.mock_packages`` will
have ``Environment.packages()`` and ``check_updates()`` patched to return
the cached list, and ``discover_all_plugins`` patched to reuse session
instances — eliminating per-test subprocess **and** plugin instantiation
overhead.
"""

import json
import subprocess
import sys

import pytest

from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.core.schema import Package


@pytest.fixture(scope='session')
def _cached_pip_packages() -> list[Package]:
    """Run ``pip list`` once for the entire session and cache as ``Package`` objects."""
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'list', '--format=json'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return []
    entries: list[dict[str, str]] = json.loads(result.stdout)
    return [Package(name=e['name'], version=e.get('version')) for e in entries]


@pytest.fixture(scope='session')
def _session_plugins() -> DiscoveredPlugins:
    """Discover all plugins once and cache the result for the session.

    The returned object is used as a template — ``_apply_mock_packages``
    returns a shallow ``.copy()`` on each call so that tests get
    independent dict containers without paying the entry-point scan or
    ``_build_from_infos`` cost on every invocation.
    """
    return discover_all_plugins(use_cache=False)
