"""Unit tests for the PyenvEnvironment plugin."""

from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PackageRef, PluginParameters
from porringer.plugin.pyenv.plugin import PyenvEnvironment
from porringer.test.mock.subprocess import fake_proc as _fake_proc
from porringer.test.pytest.tests import RuntimeProviderUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PyenvEnvironment:
    """Provide a PyenvEnvironment instance."""
    return PyenvEnvironment(_PARAMS)


class TestPyenvBasics:
    """Basic property tests."""

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
    async def test_resolve_success(environment: PyenvEnvironment) -> None:
        """Successful resolution returns the executable path."""
        prefix = '/home/user/.pyenv/versions/3.14.0'
        exe = Path(prefix) / 'bin' / 'python'
        with (
            patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout=f'{prefix}\n')),
            patch.object(Path, 'exists', return_value=True),
        ):
            result = await environment.resolve_executable('3.14.0')
            assert result == exe

    @staticmethod
    async def test_resolve_not_installed(environment: PyenvEnvironment) -> None:
        """Returns None when version is not installed."""
        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(returncode=1, stderr='not installed')):
            result = await environment.resolve_executable('3.99.0')
            assert result is None

    @staticmethod
    async def test_resolve_pyenv_missing(environment: PyenvEnvironment) -> None:
        """Returns None when pyenv is not on PATH."""
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            result = await environment.resolve_executable('3.14.0')
            assert result is None


class TestPackages:
    """Tests for packages()."""

    @staticmethod
    async def test_packages_success(environment: PyenvEnvironment) -> None:
        """Packages returns installed versions."""
        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout='3.12.4\n3.14.0\n')):
            pkgs = await environment.packages()
            expected_package_count = 2
            assert len(pkgs) == expected_package_count
            assert pkgs[0].name == '3.12.4'
            assert pkgs[1].name == '3.14.0'

    @staticmethod
    async def test_packages_empty(environment: PyenvEnvironment) -> None:
        """Empty output returns empty list."""
        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout='')):
            assert await environment.packages() == []

    @staticmethod
    async def test_packages_pyenv_missing(environment: PyenvEnvironment) -> None:
        """Returns empty list when pyenv is not on PATH."""
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            assert await environment.packages() == []


class TestPyenvEnvironmentUnit(RuntimeProviderUnitTests[PyenvEnvironment]):
    """Runs the standard environment and runtime-provider unit test suite."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PyenvEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return PyenvEnvironment
