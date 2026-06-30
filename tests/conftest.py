"""Shared pytest configuration and fixtures."""

import shutil
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core import discovery as _discovery
from porringer.backend.command.core.discovery import invalidate_plugin_cache
from porringer.backend.schema import GlobalConfiguration
from porringer.console.schema import ConsoleConfiguration
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Package
from porringer.schema import (
    LocalConfiguration,
)

# Register shared fixture modules so all tests can use them without imports.
pytest_plugins = [
    'tests.fixtures.api',
    'tests.fixtures.packages',
    'tests.fixtures.command_process',
    'tests.fixtures.disposable_environment',
    'tests.fixtures.smoke_packages',
]

# Extend the plugin-scan cache TTL so that it never expires mid-suite.
# Tests that genuinely need a fresh scan use the ``@pytest.mark.fresh_plugins``
# marker which calls ``invalidate_plugin_cache()`` explicitly.
_discovery.CACHE_TTL = 600.0


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    markers = [
        'fresh_plugins: invalidate the plugin discovery cache before this test',
        'mock_packages: use a cached package list instead of real subprocess calls',
    ]
    registered = set(config.getini('markers'))
    for marker in markers:
        if marker not in registered:
            config.addinivalue_line('markers', marker)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Invalidate the plugin cache only for tests marked ``@pytest.mark.fresh_plugins``."""
    if item.get_closest_marker('fresh_plugins'):
        invalidate_plugin_cache()


FROZEN_EXE = r'C:\app\synodic.exe'

environment_mode = pytest.mark.parametrize('is_frozen', [False, True], ids=['normal', 'frozen'])
"""Parametrize decorator that runs a test in both normal and frozen modes.

Test methods receive an ``is_frozen`` parameter.  Use
``frozen_context`` to activate the frozen environment when
``is_frozen is True``."""


@contextmanager
def frozen_context(*, which_result: str | None = None) -> Generator[None]:
    """Context manager that simulates a frozen (PyInstaller) environment.

    Patches ``sys.frozen``, ``sys.executable``, and optionally
    ``shutil.which`` in the modules that check them.

    Args:
        which_result: Value returned by ``shutil.which``.  When
            ``None``, ``shutil.which`` is not patched.
    """
    patches = [
        patch.object(sys, 'frozen', True, create=True),
        patch.object(sys, 'executable', FROZEN_EXE),
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


@contextmanager
def minimal_path_context(*, allowed_tools: set[str] | None = None) -> Generator[None]:
    """Context manager that hides all CLI tools except *allowed_tools*.

    Patches ``shutil.which`` globally so that any tool not in
    *allowed_tools* appears absent.  This catches undeclared
    auxiliary-tool dependencies.

    Args:
        allowed_tools: Tool names that should remain discoverable.
            When ``None``, **all** tools are hidden.
    """
    allowed = allowed_tools or set()
    original_which = shutil.which

    def _restricted_which(name: str, *args, **kwargs) -> str | None:
        if name in allowed:
            return original_which(name, *args, **kwargs)
        return None

    with patch('shutil.which', side_effect=_restricted_which):
        yield


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
        patch(
            'porringer.plugin.pip.plugin.PIPEnvironment.check_updates',
            _noop_check_updates,
        ),
        patch('porringer.plugin.uv.plugin.UvEnvironment.packages', _fast_packages),
        patch(
            'porringer.plugin.uv.plugin.UvEnvironment.check_updates',
            _noop_check_updates,
        ),
        # Patch discover_all_plugins at every import site
        patch(
            'porringer.backend.command.core.discovery.discover_all_plugins',
            _fast_discover,
        ),
        patch(
            'porringer.backend.command.core.action_builder.discover_all_plugins',
            _fast_discover,
        ),
        patch(
            'porringer.backend.command.core.execution.discover_all_plugins',
            _fast_discover,
        ),
        patch('porringer.backend.command.sync.discover_all_plugins', _fast_discover),
        patch('porringer.backend.command.manifest.discover_all_plugins', _fast_discover),
        patch('porringer.api.discover_all_plugins', _fast_discover),
    ):
        yield


@pytest.fixture
def fresh_plugin_cache() -> None:
    """Manually invalidate the plugin discovery cache.

    Use this fixture (or the ``@pytest.mark.fresh_plugins`` marker)
    in tests that need a guaranteed-fresh plugin scan.  Most unit
    tests mock plugin discovery and do not need this.
    """
    invalidate_plugin_cache()


@pytest.fixture(params=[False, True], ids=['pip', 'pipx'])
def installer_is_pipx(request: pytest.FixtureRequest) -> Generator[bool]:
    """Parametrize a test across pip and pipx installation modes.

    Patches ``is_pipx_installation`` in the plugin command module and yields
    the active mode as a bool so the test can assert the mode-specific command.
    """
    with patch(
        'porringer.backend.command.plugin.is_pipx_installation',
        return_value=request.param,
    ):
        yield request.param


@pytest.fixture
def stub_runtime_context() -> Generator[RuntimeContext]:
    """Patch ``Builder.resolve_runtime_context`` to return an empty context.

    Yields the ``RuntimeContext`` used as the return value for tests that only
    need runtime resolution stubbed out without a specific executable set.
    """
    ctx = RuntimeContext()
    with patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx):
        yield ctx


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
