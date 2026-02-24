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

    def test_ecosystem(self) -> None:
        assert PIMEnvironment.ecosystem() == 'python'

    def test_plugin_kind(self) -> None:
        assert PIMEnvironment.plugin_kind() == PluginKind.RUNTIME

    def test_tool_name(self) -> None:
        assert PIMEnvironment.tool_name() == 'py'

    def test_implements_runtime_provider(self, environment: PIMEnvironment) -> None:
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    def test_resolve_success(self, environment: PIMEnvironment) -> None:
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

    def test_resolve_not_installed(self, environment: PIMEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'py', stderr='not found')
            result = environment.resolve_executable('3.99')
            assert result is None

    def test_resolve_py_missing(self, environment: PIMEnvironment) -> None:
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = environment.resolve_executable('3.14')
            assert result is None

    def test_resolve_path_does_not_exist(self, environment: PIMEnvironment) -> None:
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
