"""Tests for auxiliary-tool interactions in the pip plugin."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.schema import Distribution, Package, PackageRef, PluginParameters
from porringer.plugin.pip.plugin import PIPEnvironment
from porringer.test.pytest.tests import AuxiliaryToolTests, InstallContext
from porringer.utility.utility import CommandResult

_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))

_SCENARIOS = ['tools_absent', 'tools_present', 'tools_fail_exception', 'tools_fail_nonzero']


def _make_context(scenario: str) -> Generator[InstallContext]:
    """Build an InstallContext for the given scenario."""
    env = PIPEnvironment(_MOCK_PARAMS)
    params = PackageParameters(package=PackageRef(name='test-pkg'))

    # Determine shutil.which behavior
    if scenario == 'tools_absent':
        which_return = None
    else:
        which_return = r'C:\Program Files\pymanager\pymanager.exe'

    # Determine run_command behavior for auxiliary tool calls
    if scenario == 'tools_fail_exception':

        async def run_side_effect(args, **kwargs):
            if args == ['pymanager', 'install', '--refresh']:
                raise OSError('pymanager crashed')
            return CommandResult(returncode=0, stdout='', stderr='')

        mock_run = AsyncMock(side_effect=run_side_effect)
    elif scenario == 'tools_fail_nonzero':

        async def run_side_effect_nonzero(args, **kwargs):
            if args == ['pymanager', 'install', '--refresh']:
                return CommandResult(returncode=1, stdout='', stderr='something went wrong')
            return CommandResult(returncode=0, stdout='', stderr='')

        mock_run = AsyncMock(side_effect=run_side_effect_nonzero)
    else:
        mock_run = AsyncMock(return_value=CommandResult(returncode=0, stdout='', stderr=''))

    with (
        patch.object(
            PIPEnvironment,
            '_install_simple',
            new_callable=AsyncMock,
            return_value=Package(name='test-pkg', version=None),
        ),
        patch.object(env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'test-pkg']),
        patch('porringer.plugin.pip.plugin.run_command', mock_run),
        patch('porringer.plugin.pip.plugin.shutil.which', return_value=which_return),
    ):
        yield InstallContext(plugin=env, params=params, mock_run=mock_run, scenario=scenario)


class TestPymanagerRefresh(AuxiliaryToolTests[PIPEnvironment]):
    """Auxiliary-tool tests for pip's pymanager refresh integration."""

    @pytest.fixture(name='install_context', params=_SCENARIOS)
    def fixture_install_context(self, request: pytest.FixtureRequest) -> Generator[InstallContext]:
        """Yield an InstallContext parametrized over auxiliary-tool scenarios."""
        yield from _make_context(request.param)

    @staticmethod
    async def test_refresh_not_called_on_dry_run() -> None:
        """Dry-run install does not trigger refresh even with pymanager available."""
        env = PIPEnvironment(_MOCK_PARAMS)
        params = PackageParameters(package=PackageRef(name='test-pkg'), dry=True)

        with (
            patch.object(
                PIPEnvironment,
                '_install_simple',
                new_callable=AsyncMock,
                return_value=Package(name='test-pkg', version=None),
            ),
            patch.object(env, 'install_command', return_value=['python', '-m', 'pip', 'install', 'test-pkg']),
            patch(
                'porringer.plugin.pip.plugin.shutil.which',
                return_value=r'C:\Program Files\pymanager\pymanager.exe',
            ),
            patch('porringer.plugin.pip.plugin.run_command', new_callable=AsyncMock) as mock_run,
        ):
            mock_run.return_value = CommandResult(returncode=0, stdout='', stderr='')
            result = await env.install(params)

            assert result is not None
            mock_run.assert_not_called()
