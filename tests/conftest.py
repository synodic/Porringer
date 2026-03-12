"""Shared pytest configuration and fixtures."""

import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from porringer.api import API
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core import discovery as _discovery
from porringer.backend.command.core.discovery import invalidate_plugin_cache
from porringer.backend.schema import GlobalConfiguration
from porringer.console.schema import ConsoleConfiguration
from porringer.core.schema import Package
from porringer.schema import (
    BatchSetupResults,
    LocalConfiguration,
    ProgressEventKind,
    SetupActionResult,
    SetupParameters,
    SetupResults,
)

# Register shared fixture modules so all tests can use them without imports.
pytest_plugins = [
    'tests.fixtures.manifests',
    'tests.fixtures.api',
    'tests.fixtures.packages',
]

# Extend the plugin-scan cache TTL so that it never expires mid-suite.
# Tests that genuinely need a fresh scan use the ``@pytest.mark.fresh_plugins``
# marker which calls ``invalidate_plugin_cache()`` explicitly.
_discovery.CACHE_TTL = 600.0


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        'markers',
        'fresh_plugins: invalidate the plugin discovery cache before this test',
    )
    config.addinivalue_line(
        'markers',
        'mock_packages: use a cached package list instead of real subprocess calls',
    )
    config.addinivalue_line(
        'markers',
        'frozen_app: simulate a frozen (PyInstaller) application environment',
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Invalidate the plugin cache only for tests marked ``@pytest.mark.fresh_plugins``."""
    if item.get_closest_marker('fresh_plugins'):
        invalidate_plugin_cache()


_FROZEN_EXE = r'C:\app\synodic.exe'


@contextmanager
def frozen_context(*, which_result: str | None = None) -> Iterator[None]:
    """Context manager that simulates a frozen (PyInstaller) environment.

    Patches ``sys.frozen``, ``sys.executable``, and optionally
    ``shutil.which`` in the modules that check them.

    Args:
        which_result: Value returned by ``shutil.which``.  When
            ``None``, ``shutil.which`` is not patched.
    """
    patches = [
        patch.object(sys, 'frozen', True, create=True),
        patch.object(sys, 'executable', _FROZEN_EXE),
    ]
    if which_result is not None:
        patches.append(
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=which_result,
            )
        )
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.fixture(autouse=True)
def _apply_mock_packages(
    request: pytest.FixtureRequest,
    _cached_pip_packages: list[Package],
    _session_plugins,
):
    """Patch ``Environment.packages()``, ``check_updates()``, and ``discover_all_plugins()``.

    Applied when the test is decorated with ``@pytest.mark.mock_packages``.
    The replacement coroutines return the session-cached pip package list (for
    ``packages()``) and an empty list (for ``check_updates()``).
    ``discover_all_plugins`` returns a shallow copy of the session-cached
    plugin set, avoiding repeated entry-point scanning and plugin
    instantiation.
    """
    if not request.node.get_closest_marker('mock_packages'):
        yield
        return

    cached = _cached_pip_packages
    session_plugins = _session_plugins

    async def _fast_packages(self, *, project_path=None, runtime_context=None):
        return cached

    async def _noop_check_updates(self, params):
        return []

    def _fast_discover(*, use_cache=False):
        return session_plugins.copy()

    with (
        patch('porringer.plugin.pip.plugin.PIPEnvironment.packages', _fast_packages),
        patch('porringer.plugin.pip.plugin.PIPEnvironment.check_updates', _noop_check_updates),
        patch('porringer.plugin.uv.plugin.UvEnvironment.packages', _fast_packages),
        patch('porringer.plugin.uv.plugin.UvEnvironment.check_updates', _noop_check_updates),
        # Patch discover_all_plugins at every import site
        patch('porringer.backend.command.core.discovery.discover_all_plugins', _fast_discover),
        patch('porringer.backend.command.core.action_builder.discover_all_plugins', _fast_discover),
        patch('porringer.backend.command.core.execution.discover_all_plugins', _fast_discover),
        patch('porringer.backend.command.sync.discover_all_plugins', _fast_discover),
        patch('porringer.backend.command.manifest.discover_all_plugins', _fast_discover),
        patch('porringer.api.discover_all_plugins', _fast_discover),
    ):
        yield


async def execute_via_stream(api: API, params: SetupParameters) -> BatchSetupResults:
    """Drain `execute_stream` and build `BatchSetupResults` from emitted events.

    This is a test helper that calls `execute_stream` directly — no
    separate preview step is needed.
    """
    manifests: list[SetupResults] = []
    collected: list[SetupActionResult] = []
    failed_paths: list[tuple[Path, str]] = []

    async for event in api.sync.execute_stream(params):
        if event.kind == ProgressEventKind.MANIFEST_LOADED and event.manifest:
            manifests.append(event.manifest)
        elif event.kind == ProgressEventKind.MANIFEST_FAILED and event.failed_path:
            failed_paths.append(event.failed_path)
        elif event.kind == ProgressEventKind.ACTION_COMPLETED and event.result:
            collected.append(event.result)

    # Partition collected results by manifest based on action identity
    manifest_action_sets = [set(id(a) for a in m.actions) for m in manifests]
    manifest_results: list[SetupResults] = []

    for preview, action_ids in zip(manifests, manifest_action_sets, strict=False):
        mr_results = [r for r in collected if id(r.action) in action_ids]
        sr = SetupResults(actions=preview.actions, results=mr_results)
        sr.manifest_path = preview.manifest_path
        sr.metadata = preview.metadata
        manifest_results.append(sr)

    return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)


@pytest.fixture
def fresh_plugin_cache() -> None:
    """Manually invalidate the plugin discovery cache.

    Use this fixture (or the ``@pytest.mark.fresh_plugins`` marker)
    in tests that need a guaranteed-fresh plugin scan.  Most unit
    tests mock plugin discovery and do not need this.
    """
    invalidate_plugin_cache()


@pytest.fixture
def test_config() -> ConsoleConfiguration:
    """Configuration for CLI testing."""
    console = Console(no_color=True, force_terminal=False)
    return ConsoleConfiguration(console=console)


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
