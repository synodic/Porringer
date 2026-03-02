"""Unit tests for the PIMEnvironment plugin."""

from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, PluginParameters
from porringer.plugin.pim.plugin import PIMEnvironment
from porringer.test.mock.subprocess import fake_proc as _fake_proc
from porringer.test.pytest.tests import EnvironmentUnitTests

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


@pytest.fixture
def environment() -> PIMEnvironment:
    """Provide a PIMEnvironment instance."""
    return PIMEnvironment(_PARAMS)


class TestPIMBasics:
    """Basic property tests."""

    @staticmethod
    def test_implements_runtime_provider(environment: PIMEnvironment) -> None:
        """PIMEnvironment implements RuntimeProvider."""
        assert isinstance(environment, RuntimeProvider)


class TestResolveExecutable:
    """Tests for resolve_executable."""

    @staticmethod
    async def test_resolve_success(environment: PIMEnvironment) -> None:
        """Successful resolution returns the executable path."""
        exe_path = r'C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe'
        with (
            patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout=f'{exe_path}\n')),
            patch.object(Path, 'exists', return_value=True),
        ):
            result = await environment.resolve_executable('3.14')
            assert result == Path(exe_path)

    @staticmethod
    async def test_resolve_not_installed(environment: PIMEnvironment) -> None:
        """Returns None when version is not installed."""
        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(returncode=1, stderr='not found')):
            result = await environment.resolve_executable('3.99')
            assert result is None

    @staticmethod
    async def test_resolve_py_missing(environment: PIMEnvironment) -> None:
        """Returns None when py launcher is missing."""
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            result = await environment.resolve_executable('3.14')
            assert result is None

    @staticmethod
    async def test_resolve_path_does_not_exist(environment: PIMEnvironment) -> None:
        """Returns None when resolved path does not exist."""
        with (
            patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout='/bad/path/python\n')),
            patch.object(Path, 'exists', return_value=False),
        ):
            result = await environment.resolve_executable('3.14')
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
