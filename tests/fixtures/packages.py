"""Helpers for packages."""

"""Session-scoped cached package list and plugin discovery for the ``mock_packages`` marker.

Queries installed packages via ``importlib.metadata`` **once** at session
start and pre-discovers all plugins once.  Tests decorated with
``@pytest.mark.mock_packages`` will have ``Environment.packages()`` and
``check_updates()`` patched to return the cached list, and
``discover_all_plugins`` patched to reuse session instances — eliminating
per-test subprocess **and** plugin instantiation overhead.
"""

from importlib.metadata import distributions

import pytest

from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.core.schema import Package


@pytest.fixture(scope='session')
def _cached_pip_packages() -> list[Package]:
    """Query installed packages via ``importlib.metadata`` once for the entire session.

    Uses the stdlib ``importlib.metadata`` API instead of shelling out
    to ``pip list``.  This avoids failures in environments where ``pip``
    itself is not installed (e.g. PDM-managed venvs on CI).

    .. warning::
        Do **not** replace this with ``subprocess`` + ``pip list``.
        PDM-managed venvs on CI (Ubuntu) omit ``pip``, causing the
        subprocess to fail and silently returning an empty list.
        Tests that assert on installed packages (e.g. ``packaging``)
        will then fail only on CI — not locally.
    """
    return [
        Package(name=dist.metadata['Name'], version=dist.metadata['Version'])
        for dist in distributions()
        if dist.metadata['Name'] is not None
    ]


@pytest.fixture(scope='session')
def _session_plugins() -> DiscoveredPlugins:
    """Discover all plugins once and cache the result for the session.

    The returned object is used as a template — ``_apply_mock_packages``
    returns a shallow ``.copy()`` on each call so that tests get
    independent dict containers without paying the entry-point scan or
    ``_build_from_infos`` cost on every invocation.
    """
    return discover_all_plugins(use_cache=False)
