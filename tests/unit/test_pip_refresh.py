"""Tests for pymanager alias refresh after pip install."""

from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.schema import Distribution, Package, PackageRef, PluginParameters
from porringer.plugin.pip.plugin import PIPEnvironment
from porringer.utility.utility import CommandResult

_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


@pytest.fixture
def pip_env() -> PIPEnvironment:
    """Create a PIPEnvironment instance for testing."""
    return PIPEnvironment(_MOCK_PARAMS)


@pytest.fixture
def _mock_install_simple_success():
    """Patch _install_simple to return a successful Package."""
    with patch.object(
        PIPEnvironment,
        '_install_simple',
        new_callable=AsyncMock,
        return_value=Package(name='pipx', version=None),
    ):
        yield


@pytest.fixture
def _mock_install_simple_failure():
    """Patch _install_simple to return None (failed install)."""
    with patch.object(
        PIPEnvironment,
        '_install_simple',
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield


class TestPymanagerRefresh:
    """Tests for _refresh_pymanager_aliases and its integration with install()."""

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_success')
    async def test_refresh_called_when_pymanager_available(pip_env: PIPEnvironment) -> None:
        """Successful install with pymanager on PATH triggers refresh."""
        params = PackageParameters(package=PackageRef(name='pipx'))

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=r'C:\Program Files\pymanager\pymanager.exe'),
            patch('porringer.plugin.pip.plugin.run_command', new_callable=AsyncMock) as mock_run,
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            mock_run.return_value = CommandResult(returncode=0, stdout='', stderr='')

            result = await pip_env.install(params)

            assert result is not None
            assert result.name == 'pipx'
            # _install_simple is mocked; run_command only called for refresh
            mock_run.assert_called_once_with(['pymanager', 'install', '--refresh'])

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_success')
    async def test_refresh_skipped_when_pymanager_absent(pip_env: PIPEnvironment) -> None:
        """Successful install without pymanager on PATH does not trigger refresh."""
        params = PackageParameters(package=PackageRef(name='pipx'))

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=None),
            patch('porringer.plugin.pip.plugin.run_command', new_callable=AsyncMock) as mock_run,
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            result = await pip_env.install(params)

            assert result is not None
            # refresh skipped because pymanager not on PATH
            mock_run.assert_not_called()

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_success')
    async def test_refresh_not_called_on_dry_run(pip_env: PIPEnvironment) -> None:
        """Dry-run install does not trigger refresh even with pymanager available."""
        params = PackageParameters(package=PackageRef(name='pipx'), dry=True)

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=r'C:\Program Files\pymanager\pymanager.exe'),
            patch('porringer.plugin.pip.plugin.run_command', new_callable=AsyncMock) as mock_run,
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            mock_run.return_value = CommandResult(returncode=0, stdout='', stderr='')

            result = await pip_env.install(params)

            assert result is not None
            # refresh skipped on dry run
            mock_run.assert_not_called()

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_failure')
    async def test_refresh_called_on_failed_install(pip_env: PIPEnvironment) -> None:
        """Failed install still triggers refresh (harmless no-op)."""
        params = PackageParameters(package=PackageRef(name='pipx'))

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=r'C:\Program Files\pymanager\pymanager.exe'),
            patch('porringer.plugin.pip.plugin.run_command', new_callable=AsyncMock) as mock_run,
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            mock_run.return_value = CommandResult(returncode=0, stdout='', stderr='')

            result = await pip_env.install(params)

            assert result is None
            mock_run.assert_called_once_with(['pymanager', 'install', '--refresh'])

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_success')
    async def test_refresh_failure_does_not_block_install(pip_env: PIPEnvironment) -> None:
        """If pymanager install --refresh raises, install still returns successfully."""
        params = PackageParameters(package=PackageRef(name='pipx'))

        async def side_effect(args, **kwargs):
            if args == ['pymanager', 'install', '--refresh']:
                raise OSError('pymanager crashed')
            return CommandResult(returncode=0, stdout='', stderr='')

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=r'C:\Program Files\pymanager\pymanager.exe'),
            patch('porringer.plugin.pip.plugin.run_command', side_effect=side_effect),
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            result = await pip_env.install(params)

            assert result is not None
            assert result.name == 'pipx'

    @staticmethod
    @pytest.mark.usefixtures('_mock_install_simple_success')
    async def test_refresh_nonzero_exit_does_not_block_install(pip_env: PIPEnvironment) -> None:
        """If pymanager install --refresh exits non-zero, install still returns successfully."""
        params = PackageParameters(package=PackageRef(name='pipx'))

        async def side_effect(args, **kwargs):
            if args == ['pymanager', 'install', '--refresh']:
                return CommandResult(returncode=1, stdout='', stderr='something went wrong')
            return CommandResult(returncode=0, stdout='', stderr='')

        with (
            patch('porringer.plugin.pip.plugin.shutil.which', return_value=r'C:\Program Files\pymanager\pymanager.exe'),
            patch('porringer.plugin.pip.plugin.run_command', side_effect=side_effect),
            patch.object(pip_env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'pipx']),
        ):
            result = await pip_env.install(params)

            assert result is not None
            assert result.name == 'pipx'
