"""Unit tests for the PyenvEnvironment plugin."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PackageRef, PluginKind, PluginParameters
from porringer.plugin.pyenv.plugin import PyenvEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PyenvEnvironment:
    """Provide a PyenvEnvironment instance."""
    return PyenvEnvironment(_PARAMS)


class TestPyenvBasics:
    """Basic property tests."""

    @staticmethod
    def test_ecosystem() -> None:
        """Ecosystem returns python."""
        assert PyenvEnvironment.ecosystem() == 'python'

    @staticmethod
    def test_plugin_kind() -> None:
        """Plugin kind is RUNTIME."""
        assert PyenvEnvironment.plugin_kind() == PluginKind.RUNTIME

    @staticmethod
    def test_tool_name() -> None:
        """Tool name is pyenv."""
        assert PyenvEnvironment.tool_name() == 'pyenv'

    @staticmethod
    def test_install_command(environment: PyenvEnvironment) -> None:
        """Install command uses pyenv install."""
        ref = PackageRef(name='3.14.0')
        assert environment.install_command(ref) == ['pyenv', 'install', '3.14.0']

    @staticmethod
    def test_upgrade_command(environment: PyenvEnvironment) -> None:
        """Upgrade command adds skip-existing flag."""
        ref = PackageRef(name='3.14.0')
        assert environment.upgrade_command(ref) == ['pyenv', 'install', '--skip-existing', '3.14.0']

    @staticmethod
    def test_implements_runtime_provider(environment: PyenvEnvironment) -> None:
        """PyenvEnvironment implements RuntimeProvider."""
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    @staticmethod
    def test_resolve_success(environment: PyenvEnvironment) -> None:
        """Successful resolution returns the executable path."""
        prefix = '/home/user/.pyenv/versions/3.14.0'
        exe = Path(prefix) / 'bin' / 'python'
        with (
            patch('subprocess.run') as mock_run,
            patch.object(Path, 'exists', return_value=True),
        ):
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=f'{prefix}\n', stderr='')
            result = environment.resolve_executable('3.14.0')
            assert result == exe

    @staticmethod
    def test_resolve_not_installed(environment: PyenvEnvironment) -> None:
        """Returns None when version is not installed."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'pyenv', stderr='not installed')
            result = environment.resolve_executable('3.99.0')
            assert result is None

    @staticmethod
    def test_resolve_pyenv_missing(environment: PyenvEnvironment) -> None:
        """Returns None when pyenv is not on PATH."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = environment.resolve_executable('3.14.0')
            assert result is None


class TestPackages:
    """Tests for packages()."""

    @staticmethod
    def test_packages_success(environment: PyenvEnvironment) -> None:
        """Packages returns installed versions."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='3.12.4\n3.14.0\n', stderr=''
            )
            pkgs = environment.packages()
            expected_package_count = 2
            assert len(pkgs) == expected_package_count
            assert pkgs[0].name == '3.12.4'
            assert pkgs[1].name == '3.14.0'

    @staticmethod
    def test_packages_empty(environment: PyenvEnvironment) -> None:
        """Empty output returns empty list."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
            assert environment.packages() == []

    @staticmethod
    def test_packages_pyenv_missing(environment: PyenvEnvironment) -> None:
        """Returns empty list when pyenv is not on PATH."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            assert environment.packages() == []


class TestPyenvEnvironmentUnit(EnvironmentUnitTests[PyenvEnvironment]):
    """Runs the standard environment unit test suite."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PyenvEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return PyenvEnvironment
