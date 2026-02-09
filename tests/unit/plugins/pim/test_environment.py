"""Unit tests for the PimEnvironment plugin."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PluginParameters
from porringer.plugin.pim.plugin import PimEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PimEnvironment:
    """Provide a PimEnvironment instance."""
    return PimEnvironment(_PARAMS)


class TestPimBasics:
    """Basic property tests."""

    def test_ecosystem(self) -> None:
        assert PimEnvironment.ecosystem() == 'python'

    def test_plugin_kind(self) -> None:
        from porringer.core.schema import PluginKind

        assert PimEnvironment.plugin_kind() == PluginKind.RUNTIME

    def test_tool_name(self) -> None:
        assert PimEnvironment.tool_name() == 'py'

    def test_implements_runtime_provider(self, environment: PimEnvironment) -> None:
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    def test_resolve_success(self, environment: PimEnvironment) -> None:
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
            )

    def test_resolve_not_installed(self, environment: PimEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'py', stderr='not found')
            result = environment.resolve_executable('3.99')
            assert result is None

    def test_resolve_py_missing(self, environment: PimEnvironment) -> None:
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = environment.resolve_executable('3.14')
            assert result is None

    def test_resolve_path_does_not_exist(self, environment: PimEnvironment) -> None:
        with (
            patch('subprocess.run') as mock_run,
            patch.object(Path, 'exists', return_value=False),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='/bad/path/python\n', stderr=''
            )
            result = environment.resolve_executable('3.14')
            assert result is None


class TestPimEnvironmentUnit(EnvironmentUnitTests[PimEnvironment]):
    """Runs the standard environment unit test suite."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PimEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return PimEnvironment
