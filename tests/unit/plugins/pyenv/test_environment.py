"""Unit tests for the PyenvEnvironment plugin."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.environment import PackageParameters as PkgParams
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PackageRef, PluginParameters
from porringer.plugin.pyenv.plugin import PyenvEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PyenvEnvironment:
    """Provide a PyenvEnvironment instance."""
    return PyenvEnvironment(_PARAMS)


class TestPyenvBasics:
    """Basic property tests."""

    def test_ecosystem(self) -> None:
        assert PyenvEnvironment.ecosystem() == 'python'

    def test_plugin_kind(self) -> None:
        from porringer.core.schema import PluginKind

        assert PyenvEnvironment.plugin_kind() == PluginKind.RUNTIME

    def test_tool_name(self) -> None:
        assert PyenvEnvironment.tool_name() == 'pyenv'

    def test_install_command(self, environment: PyenvEnvironment) -> None:
        ref = PackageRef(name='3.14.0')
        assert environment.install_command(ref) == ['pyenv', 'install', '3.14.0']

    def test_upgrade_command(self, environment: PyenvEnvironment) -> None:
        ref = PackageRef(name='3.14.0')
        assert environment.upgrade_command(ref) == ['pyenv', 'install', '--skip-existing', '3.14.0']

    def test_implements_runtime_provider(self, environment: PyenvEnvironment) -> None:
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    def test_resolve_success(self, environment: PyenvEnvironment) -> None:
        prefix = '/home/user/.pyenv/versions/3.14.0'
        exe = Path(prefix) / 'bin' / 'python'
        with (
            patch('subprocess.run') as mock_run,
            patch.object(Path, 'exists', return_value=True),
        ):
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=f'{prefix}\n', stderr='')
            result = environment.resolve_executable('3.14.0')
            assert result == exe

    def test_resolve_not_installed(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'pyenv', stderr='not installed')
            result = environment.resolve_executable('3.99.0')
            assert result is None

    def test_resolve_pyenv_missing(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = environment.resolve_executable('3.14.0')
            assert result is None


class TestPackages:
    """Tests for packages()."""

    def test_packages_success(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='3.12.4\n3.14.0\n', stderr=''
            )
            pkgs = environment.packages()
            assert len(pkgs) == 2
            assert pkgs[0].name == '3.12.4'
            assert pkgs[1].name == '3.14.0'

    def test_packages_empty(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
            assert environment.packages() == []

    def test_packages_pyenv_missing(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run', side_effect=FileNotFoundError):
            assert environment.packages() == []


class TestInstall:
    """Tests for install()."""

    def test_install_success(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
            params = PkgParams(package=PackageRef(name='3.14.0'))
            result = environment.install(params)
            assert result is not None
            assert result.name == '3.14.0'

    def test_install_dry_run(self, environment: PyenvEnvironment) -> None:
        params = PkgParams(package=PackageRef(name='3.14.0'), dry=True)
        result = environment.install(params)
        assert result is not None
        assert result.name == '3.14.0'

    def test_install_failure(self, environment: PyenvEnvironment) -> None:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='error')
            params = PkgParams(package=PackageRef(name='3.14.0'))
            assert environment.install(params) is None


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
