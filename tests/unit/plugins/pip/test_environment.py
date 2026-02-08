"""Tests for the pip environment plugin."""

import json
import subprocess
from collections.abc import Callable
from typing import Any

import pytest
from packaging.version import Version

from porringer.core.schema import Distribution, Package, PluginParameters
from porringer.plugin.pip.plugin import PipEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[PipEnvironment]):
    """The tests for the pip environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PipEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PipEnvironment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PACKAGES = [
    {'name': 'ruff', 'version': '0.15.0'},
    {'name': 'pytest', 'version': '9.0.2'},
    {'name': 'pydantic', 'version': '2.12.5'},
]
"""Simulated package list shared across scenarios."""


def _make_env() -> PipEnvironment:
    """Create a fresh PipEnvironment for testing (no cached packages)."""
    params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
    return PipEnvironment(params)


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    """Helper to build a successful CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr='')


def _mock_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Replace subprocess.run with *handler*."""
    monkeypatch.setattr(subprocess, 'run', handler)


# ---------------------------------------------------------------------------
# Scenario: venv with pip installed (standard venv / virtualenv)
# ---------------------------------------------------------------------------


class TestVenvWithPip:
    """Simulate a virtual environment where ``python -m pip`` works normally."""

    @staticmethod
    def test_lists_packages(monkeypatch: pytest.MonkeyPatch) -> None:
        """Pip list succeeds — packages returned directly, no fallback needed."""
        pip_json = json.dumps(_SAMPLE_PACKAGES)

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return _ok(pip_json)

        _mock_subprocess(monkeypatch, run)
        result = _make_env().packages()

        assert result == [Package(name=p['name'], version=p['version']) for p in _SAMPLE_PACKAGES]

    @staticmethod
    def test_skips_entries_without_name(monkeypatch: pytest.MonkeyPatch) -> None:
        """Entries missing a ``name`` key are silently dropped."""
        pip_json = json.dumps(
            [
                {'name': 'ruff', 'version': '0.15.0'},
                {'version': '1.0.0'},
                {'name': None, 'version': '2.0.0'},
            ]
        )
        _mock_subprocess(monkeypatch, lambda *a, **kw: _ok(pip_json))

        result = _make_env().packages()
        assert len(result) == 1
        assert result[0].name == 'ruff'

    @staticmethod
    def test_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
        """A venv with nothing installed returns an empty list."""
        _mock_subprocess(monkeypatch, lambda *a, **kw: _ok('[]'))

        assert _make_env().packages() == []


# ---------------------------------------------------------------------------
# Scenario: uv-created venv (no pip module, importlib.metadata fallback)
# ---------------------------------------------------------------------------


class TestVenvWithoutPip:
    """Simulate a uv-created venv with no ``pip`` but ``importlib.metadata``.

    This tests the fallback mechanism that uses importlib.metadata when
    ``python -m pip`` is not available to enumerate installed distributions.
    """

    @staticmethod
    def test_fallback_lists_packages(monkeypatch: pytest.MonkeyPatch) -> None:
        """Pip list fails → importlib.metadata fallback returns packages."""
        importlib_json = json.dumps(_SAMPLE_PACKAGES)
        call_count = 0

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise subprocess.CalledProcessError(1, 'pip')
            return _ok(importlib_json)

        _mock_subprocess(monkeypatch, run)
        result = _make_env().packages()

        expected_call_count = 2  # pip list + importlib.metadata fallback
        assert call_count == expected_call_count
        assert result == [Package(name=p['name'], version=p['version']) for p in _SAMPLE_PACKAGES]

    @staticmethod
    def test_both_methods_fail(monkeypatch: pytest.MonkeyPatch) -> None:
        """Both pip list and importlib.metadata fail → empty list."""

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, 'python')

        _mock_subprocess(monkeypatch, run)
        assert _make_env().packages() == []


# ---------------------------------------------------------------------------
# Scenario: global Python (no venv active, python not on PATH, etc.)
# ---------------------------------------------------------------------------


class TestGlobalEnvironment:
    """Simulate global (system-wide) Python and edge cases around PATH."""

    @staticmethod
    def test_python_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``python`` is not found, return empty without crashing."""

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError

        _mock_subprocess(monkeypatch, run)
        assert _make_env().packages() == []

    @staticmethod
    def test_pip_returns_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
        """Corrupt pip output is handled gracefully (returns empty, no fallback)."""
        _mock_subprocess(monkeypatch, lambda *a, **kw: _ok('not json'))

        assert _make_env().packages() == []

    @staticmethod
    def test_pip_returns_global_packages(monkeypatch: pytest.MonkeyPatch) -> None:
        """A global Python with pip works the same as a venv with pip."""
        global_pkgs = [
            {'name': 'setuptools', 'version': '75.0.0'},
            {'name': 'wheel', 'version': '0.45.0'},
        ]
        _mock_subprocess(monkeypatch, lambda *a, **kw: _ok(json.dumps(global_pkgs)))

        result = _make_env().packages()
        expected_package_count = 2
        assert len(result) == expected_package_count
        assert result[0] == Package(name='setuptools', version='75.0.0')


# ---------------------------------------------------------------------------
# Caching behaviour
# ---------------------------------------------------------------------------


class TestCaching:
    """Verify that packages() results are cached per-instance."""

    @staticmethod
    def test_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
        """subprocess.run is only called once, subsequent calls use cache."""
        call_count = 0

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return _ok(json.dumps([{'name': 'ruff', 'version': '0.15.0'}]))

        _mock_subprocess(monkeypatch, run)
        env = _make_env()

        first = env.packages()
        second = env.packages()

        assert first is second
        assert call_count == 1

    @staticmethod
    def test_separate_instances_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
        """Each PipEnvironment instance has its own cache."""
        call_count = 0

        def run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return _ok(json.dumps([{'name': 'ruff', 'version': '0.15.0'}]))

        _mock_subprocess(monkeypatch, run)
        env1 = _make_env()
        env2 = _make_env()

        env1.packages()
        env2.packages()

        expected_calls = 2
        assert call_count == expected_calls
