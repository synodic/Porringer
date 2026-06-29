"""Helpers for test disposable environment."""

"""Tests for disposable tool-home smoke fixtures."""

import os
from pathlib import Path

import pytest

from porringer.utility.tool_environment import DISPOSABLE_ENVIRONMENT_BINDINGS
from tests.fixtures.disposable_environment import DisposableToolEnvironmentFactory


def test_disposable_tool_environment_sets_user_and_tool_homes(disposable_tool_environment) -> None:
    """The fixture points user/tool state at temporary directories."""
    env = disposable_tool_environment

    for directory in env.directories:
        assert directory.is_dir()

    for name, attr in DISPOSABLE_ENVIRONMENT_BINDINGS.items():
        assert os.environ[name] == str(getattr(env, attr))


def test_default_path_contains_only_disposable_tool_bins(
    disposable_tool_environment_factory: DisposableToolEnvironmentFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The default fixture hides host PATH entries."""
    host_bin = tmp_path / 'host-bin'
    host_bin.mkdir()
    monkeypatch.setenv('PATH', str(host_bin))

    env = disposable_tool_environment_factory()

    assert host_bin not in env.path_entries
    assert list(env.path_entries) == list(env.tool_bin_dirs)
    assert os.environ['PATH'] == os.pathsep.join(str(path) for path in env.tool_bin_dirs)


def test_allowed_host_tools_preserve_only_their_parent_directories(
    disposable_tool_environment_factory: DisposableToolEnvironmentFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allowed host tools contribute their containing directory to PATH."""
    host_bin = tmp_path / 'host-bin'
    host_bin.mkdir()
    tool_name = 'host-tool.cmd' if os.name == 'nt' else 'host-tool'
    tool_path = host_bin / tool_name
    tool_path.write_text('')
    if os.name != 'nt':
        tool_path.chmod(0o755)
    monkeypatch.setenv('PATH', str(host_bin))

    env = disposable_tool_environment_factory(allowed_host_tools=[tool_name])

    assert host_bin in env.path_entries
    assert env.path_entries[-1] == host_bin


def test_extra_path_entries_are_prepended_and_deduplicated(
    disposable_tool_environment_factory: DisposableToolEnvironmentFactory,
    tmp_path: Path,
) -> None:
    """Test-specific shims can be prepended without duplicate PATH entries."""
    shim_dir = tmp_path / 'shim-bin'
    shim_dir.mkdir()

    env = disposable_tool_environment_factory(extra_path_entries=[shim_dir, shim_dir])

    assert env.path_entries[0] == shim_dir
    assert env.path_entries.count(shim_dir) == 1
