"""Unit tests for the PIMEnvironment plugin."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PluginKind, PluginParameters
from porringer.plugin.pim.plugin import PIMEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PIMEnvironment:
    """Provide a PIMEnvironment instance."""
    return PIMEnvironment(_PARAMS)


class TestPIMBasics:
    """Basic property tests."""

    @staticmethod
    def test_ecosystem() -> None:
        """Ecosystem returns python."""
        assert PIMEnvironment.ecosystem() == 'python'

    @staticmethod
    def test_plugin_kind() -> None:
        """Plugin kind is RUNTIME."""
        assert PIMEnvironment.plugin_kind() == PluginKind.RUNTIME

    @staticmethod
    def test_tool_name() -> None:
        """Tool name is py."""
        assert PIMEnvironment.tool_name() == 'py'

    @staticmethod
    def test_implements_runtime_provider(environment: PIMEnvironment) -> None:
        """PIMEnvironment implements RuntimeProvider."""
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    @staticmethod
    def test_resolve_success(environment: PIMEnvironment) -> None:
        """Successful resolution returns the executable path."""
        exe_path = r'C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe'
        with (
            patch('subprocess.run') as mock_run,
            patch.object(Path, 'exists', return_value=True),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=f'{exe_path}\n', stderr=''
            )
            result = environment.resolve_executable('3.14')
            assert result == Path(exe_path)
            mock_run.assert_called_once_with(
                ['py', '-3.14', '-c', 'import sys; print(sys.executable)'],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )

    @staticmethod
    def test_resolve_not_installed(environment: PIMEnvironment) -> None:
        """Returns None when version is not installed."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'py', stderr='not found')
            result = environment.resolve_executable('3.99')
            assert result is None

    @staticmethod
    def test_resolve_py_missing(environment: PIMEnvironment) -> None:
        """Returns None when py launcher is missing."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = environment.resolve_executable('3.14')
            assert result is None

    @staticmethod
    def test_resolve_path_does_not_exist(environment: PIMEnvironment) -> None:
        """Returns None when resolved path does not exist."""
        with (
            patch('subprocess.run') as mock_run,
            patch.object(Path, 'exists', return_value=False),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='/bad/path/python\n', stderr=''
            )
            result = environment.resolve_executable('3.14')
            assert result is None


class TestPIMEnvironmentUnit(EnvironmentUnitTests[PIMEnvironment]):
    """Runs the standard environment unit test suite."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PIMEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return PIMEnvironment
