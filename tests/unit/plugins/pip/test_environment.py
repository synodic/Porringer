"""Tests plugin schemas"""

import json
import subprocess

import pytest
from packaging.version import Version

from porringer.core.schema import Distribution, Package, PluginParameters
from porringer.plugin.pip.plugin import PipEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests

# Test constants
EXPECTED_PACKAGE_COUNT = 2


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


def _make_pip_environment() -> PipEnvironment:
    """Helper to create a PipEnvironment instance for testing."""
    params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
    return PipEnvironment(params)


class TestPackages:
    """Tests for PipEnvironment.packages() subprocess-based implementation."""

    @staticmethod
    def test_packages_parses_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should parse pip list --format=json output into Package objects."""
        pip_output = json.dumps([
            {'name': 'ruff', 'version': '0.15.0'},
            {'name': 'pytest', 'version': '9.0.2'},
        ])

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=pip_output, stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert len(result) == EXPECTED_PACKAGE_COUNT
        assert result[0] == Package(name='ruff', version='0.15.0')
        assert result[1] == Package(name='pytest', version='9.0.2')

    @staticmethod
    def test_packages_returns_empty_on_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should return an empty list when pip list fails."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, 'pip')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert result == []

    @staticmethod
    def test_packages_returns_empty_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should return an empty list when pip outputs invalid JSON."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout='not json', stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert result == []

    @staticmethod
    def test_packages_returns_empty_on_missing_python(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should return an empty list when python is not found."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert result == []

    @staticmethod
    def test_packages_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should only call subprocess once and cache the result."""
        call_count = 0
        pip_output = json.dumps([{'name': 'ruff', 'version': '0.15.0'}])

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=pip_output, stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()

        first = env.packages()
        second = env.packages()

        assert first == second
        assert call_count == 1

    @staticmethod
    def test_packages_skips_entries_without_name(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should skip entries missing a name field."""
        pip_output = json.dumps([
            {'name': 'ruff', 'version': '0.15.0'},
            {'version': '1.0.0'},
            {'name': None, 'version': '2.0.0'},
        ])

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=pip_output, stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert len(result) == 1
        assert result[0].name == 'ruff'

    @staticmethod
    def test_packages_handles_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
        """packages() should return an empty list when pip reports no packages."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout='[]', stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        env = _make_pip_environment()
        result = env.packages()

        assert result == []


class TestIsAvailable:
    """Tests for PipEnvironment.is_available()."""

    @staticmethod
    def test_is_available_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
        """is_available() should return True when pip --version succeeds."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout='pip 24.0', stderr='')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        assert PipEnvironment.is_available() is True

    @staticmethod
    def test_is_available_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
        """is_available() should return False when pip --version fails."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='error')

        monkeypatch.setattr(subprocess, 'run', mock_run)
        assert PipEnvironment.is_available() is False

    @staticmethod
    def test_is_available_returns_false_on_missing_python(monkeypatch: pytest.MonkeyPatch) -> None:
        """is_available() should return False when python is not on PATH."""

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, 'run', mock_run)
        assert PipEnvironment.is_available() is False
