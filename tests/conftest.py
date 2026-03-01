"""Shared pytest configuration and fixtures."""

import tempfile
from pathlib import Path

import pytest
from rich.console import Console

from porringer.api import API
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import invalidate_plugin_cache
from porringer.backend.schema import GlobalConfiguration
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import (
    BatchSetupResults,
    LocalConfiguration,
    ProgressEventKind,
    SetupActionResult,
    SetupParameters,
    SetupResults,
)


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


@pytest.fixture(autouse=True)
def _invalidate_plugin_cache() -> None:
    """Clear the module-level plugin cache before every test.

    The discovery cache (30 s TTL) holds live plugin instances.
    Clearing it ensures each test starts with freshly-discovered
    plugins and avoids any stale state leaking across test boundaries.
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
