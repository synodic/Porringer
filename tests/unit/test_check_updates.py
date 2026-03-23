"""Tests for check_updates implementations across all environment plugins.

Validates that each plugin's ``check_updates`` method correctly queries
its respective package registry and handles ``include_prereleases``,
error conditions, and package filtering.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from packaging.version import Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Distribution, Package, PackageRef, PluginParameters
from porringer.plugin.apt.plugin import APTEnvironment
from porringer.plugin.brew.plugin import BrewEnvironment
from porringer.plugin.deno.plugin import DenoEnvironment
from porringer.plugin.npm.plugin import NPMEnvironment
from porringer.plugin.pim.plugin import PIMEnvironment
from porringer.plugin.pip.plugin import PIPEnvironment
from porringer.plugin.pipx.plugin import PIPXEnvironment
from porringer.plugin.pyenv.plugin import PyenvEnvironment
from porringer.plugin.winget.plugin import WingetEnvironment
from porringer.test.mock.subprocess import fake_proc as _fake_proc
from tests.fixtures.http import setup_async_client as _setup_async_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


def _make_params(
    packages: list[str],
    *,
    include_prereleases: bool = False,
) -> CheckUpdatesParameters:
    """Build a ``CheckUpdatesParameters`` from plain package name strings."""
    return CheckUpdatesParameters(
        packages=[PackageRef.model_validate(p) for p in packages],
        include_prereleases=include_prereleases,
    )


# =========================================================================
# PythonEnvironment._check_pypi_updates (shared by pip, pipx, uv)
# =========================================================================


class TestCheckPypiUpdates:
    """Tests for the shared PyPI helper on PythonEnvironment."""

    @staticmethod
    async def test_stable_version_returned() -> None:
        """Stable-only query returns info.version."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        pypi_data = {'info': {'version': '1.2.3'}, 'releases': {'1.2.3': []}}
        response = MagicMock()
        response.json.return_value = pypi_data
        response.raise_for_status = MagicMock()

        with patch('aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await env._check_pypi_updates(_make_params(['some-package']))

        assert len(result) == 1
        assert result[0].name == 'some-package'
        assert result[0].version == '1.2.3'

    @staticmethod
    async def test_prerelease_version_returned() -> None:
        """Pre-release query scans all release keys for the highest."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        pypi_data = {
            'info': {'version': '1.2.3'},
            'releases': {
                '1.2.3': [],
                '1.3.0a1': [],
                '1.3.0.dev2': [],
                '1.2.4': [],
            },
        }
        response = MagicMock()
        response.json.return_value = pypi_data
        response.raise_for_status = MagicMock()

        with patch('aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await env._check_pypi_updates(_make_params(['some-package'], include_prereleases=True))

        assert len(result) == 1
        assert result[0].version == '1.3.0a1'

    @staticmethod
    async def test_http_error_returns_empty() -> None:
        """Network failures are handled gracefully."""
        env = PIPXEnvironment(_MOCK_PARAMS)

        with patch('aiohttp.ClientSession') as mock_client:
            mock_instance = MagicMock()
            error_resp = MagicMock()
            error_resp.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError('timeout'))
            error_resp.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = MagicMock(return_value=error_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await env._check_pypi_updates(_make_params(['some-package']))

        assert result == []

    @staticmethod
    async def test_multiple_packages() -> None:
        """Multiple packages are queried independently."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        data_a = {'info': {'version': '2.0.0'}, 'releases': {}}
        data_b = {'info': {'version': '3.0.0'}, 'releases': {}}

        call_count = 0

        def mock_get(url: str) -> MagicMock:
            nonlocal call_count
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = AsyncMock(return_value=data_a if call_count == 0 else data_b)
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            call_count += 1
            return resp

        with patch('aiohttp.ClientSession') as mock_client:
            instance = MagicMock(get=mock_get)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await env._check_pypi_updates(_make_params(['pkg-a', 'pkg-b']))

        expected_count = 2
        assert len(result) == expected_count
        assert result[0].version == '2.0.0'
        assert result[1].version == '3.0.0'

    @staticmethod
    async def test_prerelease_info_version_falls_back_to_stable_release() -> None:
        """When info.version is a pre-release and include_prereleases=False, scan releases for stable."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        pypi_data = {
            'info': {'version': '0.9.15.dev3'},
            'releases': {
                '0.9.14': [],
                '0.9.15.dev3': [],
                '0.9.13': [],
            },
        }
        response = MagicMock()
        response.json.return_value = pypi_data
        response.raise_for_status = MagicMock()

        with patch('aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await env._check_pypi_updates(_make_params(['cppython']))

        assert len(result) == 1
        assert result[0].version == '0.9.14'

    @staticmethod
    async def test_prerelease_info_version_with_no_stable_releases() -> None:
        """When info.version is a pre-release and no stable releases exist, return empty."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        pypi_data = {
            'info': {'version': '1.0.0a1'},
            'releases': {
                '1.0.0a1': [],
                '1.0.0.dev1': [],
            },
        }
        response = MagicMock()
        response.json.return_value = pypi_data
        response.raise_for_status = MagicMock()

        with patch('aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await env._check_pypi_updates(_make_params(['some-package']))

        assert result == []

    @staticmethod
    async def test_stable_info_version_not_affected() -> None:
        """When info.version is stable and include_prereleases=False, return it directly."""
        env = PIPXEnvironment(_MOCK_PARAMS)
        pypi_data = {
            'info': {'version': '2.0.0'},
            'releases': {
                '2.0.0': [],
                '2.1.0.dev1': [],
            },
        }
        response = MagicMock()
        response.json.return_value = pypi_data
        response.raise_for_status = MagicMock()

        with patch('aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await env._check_pypi_updates(_make_params(['some-package']))

        assert len(result) == 1
        assert result[0].version == '2.0.0'


# =========================================================================
# PIPXEnvironment / UvEnvironment â€” inherit from PythonEnvironment
# =========================================================================
# These plugins inherit check_updates from PythonEnvironment, which
# delegates to _check_pypi_updates (tested above).  The abstract method
# enforcement ensures the override exists.  No per-plugin tests needed.


# =========================================================================
# PIPEnvironment â€” uses pip list --outdated with PyPI fallback
# =========================================================================


class TestPipCheckUpdates:
    """PIPEnvironment.check_updates uses native pip then falls back to PyPI."""

    @staticmethod
    async def test_native_pip_outdated() -> None:
        """Native pip outdated returns filtered results."""
        env = PIPEnvironment(_MOCK_PARAMS)
        outdated_json = json.dumps([
            {'name': 'ruff', 'version': '0.8.0', 'latest_version': '0.9.0', 'latest_filetype': 'wheel'},
            {'name': 'black', 'version': '23.0', 'latest_version': '24.0', 'latest_filetype': 'wheel'},
        ])

        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout=outdated_json)):
            result = await env.check_updates(_make_params(['ruff']))

        assert len(result) == 1
        assert result[0].name == 'ruff'
        assert result[0].version == '0.9.0'

    @staticmethod
    async def test_prereleases_flag_passed() -> None:
        """Prereleases flag is passed to pip command."""
        env = PIPEnvironment(_MOCK_PARAMS)
        outdated_json = json.dumps([])

        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout=outdated_json)) as mock_exec:
            await env.check_updates(_make_params(['ruff'], include_prereleases=True))

        args = mock_exec.call_args[0]
        assert '--pre' in args

    @staticmethod
    async def test_falls_back_to_pypi_on_failure() -> None:
        """Falls back to PyPI on pip command failure."""
        env = PIPEnvironment(_MOCK_PARAMS)

        with (
            patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError),
            patch.object(
                env,
                '_check_pypi_updates',
                new_callable=AsyncMock,
                return_value=[Package(name='ruff', version='1.0.0')],
            ) as pypi_mock,
        ):
            result = await env.check_updates(_make_params(['ruff']))

        pypi_mock.assert_called_once()
        assert result[0].version == '1.0.0'

    @staticmethod
    async def test_all_packages_returned_when_no_filter() -> None:
        """All packages returned when no filter is specified."""
        env = PIPEnvironment(_MOCK_PARAMS)
        outdated_json = json.dumps([
            {'name': 'ruff', 'version': '0.8.0', 'latest_version': '0.9.0'},
            {'name': 'black', 'version': '23.0', 'latest_version': '24.0'},
        ])

        with patch('asyncio.create_subprocess_exec', return_value=_fake_proc(stdout=outdated_json)):
            result = await env.check_updates(CheckUpdatesParameters(packages=[], include_prereleases=False))

        expected_count = 2
        assert len(result) == expected_count


# =========================================================================
# NPMEnvironment â€” npm registry via shared helper
# =========================================================================


class TestNpmCheckUpdates:
    """NPMEnvironment.check_updates delegates to _check_npm_registry."""

    @staticmethod
    async def test_stable_version() -> None:
        """Stable version is returned from npm registry."""
        env = NPMEnvironment(_MOCK_PARAMS)
        expected = [Package(name='typescript', version='10.0.0')]

        with patch.object(env, '_check_npm_registry', new_callable=AsyncMock, return_value=expected) as mock:
            result = await env.check_updates(_make_params(['typescript']))

        mock.assert_called_once()
        assert result == expected

    @staticmethod
    async def test_prereleases_forwarded() -> None:
        """Prereleases flag is forwarded to npm registry."""
        env = NPMEnvironment(_MOCK_PARAMS)
        with patch.object(env, '_check_npm_registry', new_callable=AsyncMock, return_value=[]) as mock:
            await env.check_updates(_make_params(['typescript'], include_prereleases=True))

        _, kwargs = mock.call_args
        assert kwargs['include_prereleases'] is True


# =========================================================================
# PNPMEnvironment / BunEnvironment â€” same shared helper, tested via npm
# =========================================================================
# These plugins delegate identically to _check_npm_registry.
# The abstract method enforcement ensures they override check_updates.
# The shared helper is tested in TestNpmRegistryHelper below.
# No per-plugin tests needed.


# =========================================================================
# DenoEnvironment â€” npm and JSR registries
# =========================================================================


class TestDenoCheckUpdates:
    """DenoEnvironment.check_updates handles npm: and jsr: prefixes."""

    @staticmethod
    async def test_npm_package() -> None:
        """npm-prefixed package queries npm registry."""
        env = DenoEnvironment(_MOCK_PARAMS)

        with patch.object(
            env, '_check_npm_registry', new_callable=AsyncMock, return_value=[Package(name='chalk', version='2.0.0')]
        ) as mock_npm:
            result = await env.check_updates(_make_params(['npm:chalk']))

        mock_npm.assert_called_once()
        assert len(result) == 1
        assert result[0].name == 'npm:chalk'
        assert result[0].version == '2.0.0'

    @staticmethod
    async def test_jsr_package() -> None:
        """jsr-prefixed package queries JSR registry."""
        env = DenoEnvironment(_MOCK_PARAMS)
        jsr_data = {'latest': '0.5.0'}
        response = MagicMock()
        response.json.return_value = jsr_data
        response.raise_for_status = MagicMock()

        with (
            patch.object(env, '_check_npm_registry', new_callable=AsyncMock, return_value=[]),
            patch('porringer.plugin.deno.plugin.aiohttp.ClientSession') as mock_client,
        ):
            _setup_async_client(mock_client, response)
            result = await env.check_updates(_make_params(['jsr:@std/path']))

        assert len(result) == 1
        assert result[0].version == '0.5.0'

    @staticmethod
    async def test_bare_name_queries_npm() -> None:
        """Bare names (no prefix) should route to _check_npm_registry."""
        env = DenoEnvironment(_MOCK_PARAMS)

        with patch.object(
            env, '_check_npm_registry', new_callable=AsyncMock, return_value=[Package(name='chalk', version='4.0.0')]
        ) as mock_npm:
            result = await env.check_updates(_make_params(['chalk']))

        mock_npm.assert_called_once()
        assert len(result) == 1
        assert result[0].version == '4.0.0'


# =========================================================================
# BrewEnvironment â€” brew outdated + brew info
# =========================================================================


class TestBrewCheckUpdates:
    """BrewEnvironment.check_updates uses ``brew outdated --json``."""

    @staticmethod
    async def test_outdated_with_info() -> None:
        """Outdated packages include info version."""
        env = BrewEnvironment(_MOCK_PARAMS)
        outdated_data = [{'name': 'git', 'current_version': '2.43.0'}]
        info_data = {'formulae': [{'versions': {'stable': '2.44.0'}}]}

        with patch.object(env, '_run_json_command', new_callable=AsyncMock) as mock_cmd:
            mock_cmd.side_effect = [outdated_data, info_data]
            result = await env.check_updates(_make_params(['git']))

        assert len(result) == 1
        assert result[0].name == 'git'
        assert result[0].version == '2.44.0'

    @staticmethod
    async def test_outdated_filters_by_requested() -> None:
        """Outdated results are filtered by requested packages."""
        env = BrewEnvironment(_MOCK_PARAMS)
        outdated_data = [
            {'name': 'git', 'current_version': '2.43.0'},
            {'name': 'wget', 'current_version': '1.21'},
        ]
        info_data = {'formulae': [{'versions': {'stable': '2.44.0'}}]}

        with patch.object(env, '_run_json_command', new_callable=AsyncMock) as mock_cmd:
            mock_cmd.side_effect = [outdated_data, info_data]
            result = await env.check_updates(_make_params(['git']))

        assert len(result) == 1
        assert result[0].name == 'git'

    @staticmethod
    async def test_no_outdated_returns_empty() -> None:
        """No outdated packages returns empty list."""
        env = BrewEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_json_command', new_callable=AsyncMock, return_value=[]):
            result = await env.check_updates(_make_params(['git']))

        assert result == []

    @staticmethod
    async def test_command_failure_returns_empty() -> None:
        """Command failure returns empty list."""
        env = BrewEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_json_command', new_callable=AsyncMock, return_value=None):
            result = await env.check_updates(_make_params(['git']))

        assert result == []


# =========================================================================
# WingetEnvironment â€” winget upgrade (text parsing)
# =========================================================================


class TestWingetCheckUpdates:
    """WingetEnvironment.check_updates parses ``winget upgrade`` output."""

    @staticmethod
    async def test_parses_upgrade_output() -> None:
        """Parses winget upgrade output correctly."""
        env = WingetEnvironment(_MOCK_PARAMS)
        # Typical winget upgrade output with fixed-width columns
        output = (
            'Name                    Id                      Version  Available Source\n'
            '--------------------------------------------------------------------------\n'
            'Python 3.12             Python.Python.3.12      3.12.0   3.12.1    winget\n'
        )

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=output):
            result = await env.check_updates(_make_params(['Python.Python.3.12']))

        assert len(result) == 1
        assert result[0].name == 'Python.Python.3.12'
        assert result[0].version == '3.12.1'

    @staticmethod
    async def test_command_failure_returns_empty() -> None:
        """Command failure returns empty list."""
        env = WingetEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=None):
            result = await env.check_updates(_make_params(['Python.Python.3.12']))

        assert result == []


# =========================================================================
# APTEnvironment â€” apt-cache policy
# =========================================================================


class TestAptCheckUpdates:
    """APTEnvironment.check_updates uses ``apt-cache policy``."""

    @staticmethod
    async def test_parses_candidate() -> None:
        """Parses candidate version from apt-cache policy."""
        env = APTEnvironment(_MOCK_PARAMS)
        policy_output = 'python3:\n  Installed: 3.11.6-1\n  Candidate: 3.12.0-1\n  Version table:\n'

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=policy_output):
            result = await env.check_updates(_make_params(['python3']))

        assert len(result) == 1
        assert result[0].name == 'python3'
        assert result[0].version == '3.12.0-1'

    @staticmethod
    async def test_no_candidate_returns_empty() -> None:
        """No candidate version returns empty list."""
        env = APTEnvironment(_MOCK_PARAMS)
        policy_output = 'nonexistent:\n  Installed: (none)\n  Candidate: (none)\n'

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=policy_output):
            result = await env.check_updates(_make_params(['nonexistent']))

        assert result == []

    @staticmethod
    async def test_command_failure_returns_empty() -> None:
        """Command failure returns empty list."""
        env = APTEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=None):
            result = await env.check_updates(_make_params(['python3']))

        assert result == []


# =========================================================================
# PyenvEnvironment â€” pyenv install --list
# =========================================================================


class TestPyenvCheckUpdates:
    """PyenvEnvironment.check_updates uses ``pyenv install --list``."""

    @staticmethod
    async def test_finds_latest_matching_version() -> None:
        """Finds latest matching version from pyenv list."""
        env = PyenvEnvironment(_MOCK_PARAMS)
        list_output = '  3.11.8\n  3.12.0\n  3.12.1\n  3.12.2\n  3.13.0a3\n'

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=list_output):
            result = await env.check_updates(_make_params(['3.12']))

        assert len(result) == 1
        assert result[0].name == '3.12'
        assert result[0].version == '3.12.2'

    @staticmethod
    async def test_excludes_prereleases_by_default() -> None:
        """Excludes prereleases by default."""
        env = PyenvEnvironment(_MOCK_PARAMS)
        list_output = '  3.13.0a3\n  3.13.0b1\n  3.12.2\n'

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=list_output):
            result = await env.check_updates(_make_params(['3.13']))

        # No stable 3.13 exists
        assert result == []

    @staticmethod
    async def test_includes_prereleases_when_requested() -> None:
        """Includes prereleases when requested."""
        env = PyenvEnvironment(_MOCK_PARAMS)
        list_output = '  3.13.0a3\n  3.13.0b1\n  3.12.2\n'

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=list_output):
            result = await env.check_updates(_make_params(['3.13'], include_prereleases=True))

        assert len(result) == 1
        assert result[0].version == '3.13.0b1'

    @staticmethod
    async def test_command_failure_returns_empty() -> None:
        """Command failure returns empty list."""
        env = PyenvEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_text_command', new_callable=AsyncMock, return_value=None):
            result = await env.check_updates(_make_params(['3.12']))

        assert result == []


# =========================================================================
# PIMEnvironment â€” py list --online
# =========================================================================


class TestPimCheckUpdates:
    """PIMEnvironment.check_updates uses ``py list --online``."""

    @staticmethod
    async def test_finds_latest_matching_version() -> None:
        """Finds latest matching version from pim list."""
        env = PIMEnvironment(_MOCK_PARAMS)
        data = {
            'versions': [
                {'tag': '3.12.0', 'sort-version': '3.12.0'},
                {'tag': '3.12.1', 'sort-version': '3.12.1'},
                {'tag': '3.11.8', 'sort-version': '3.11.8'},
            ]
        }

        with patch.object(env, '_run_json_command', new_callable=AsyncMock, return_value=data):
            result = await env.check_updates(_make_params(['3.12']))

        assert len(result) == 1
        assert result[0].version == '3.12.1'

    @staticmethod
    async def test_command_failure_returns_empty() -> None:
        """Command failure returns empty list."""
        env = PIMEnvironment(_MOCK_PARAMS)

        with patch.object(env, '_run_json_command', new_callable=AsyncMock, return_value=None):
            result = await env.check_updates(_make_params(['3.12']))

        assert result == []


# =========================================================================
# Shared _check_npm_registry helper (on Environment)
# =========================================================================


class TestNpmRegistryHelper:
    """Tests for Environment._check_npm_registry shared helper."""

    @staticmethod
    async def test_stable_version() -> None:
        """Stable version returned from npm registry."""
        registry_data = {
            'dist-tags': {'latest': '10.0.0'},
            'versions': {'9.0.0': {}, '10.0.0': {}, '11.0.0-beta.1': {}},
        }
        response = MagicMock()
        response.json.return_value = registry_data
        response.raise_for_status = MagicMock()

        with patch('porringer.core.plugin_schema.environment.aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await Environment._check_npm_registry([PackageRef.model_validate('typescript')])

        assert len(result) == 1
        assert result[0].version == '10.0.0'

    @staticmethod
    async def test_prerelease_version() -> None:
        """Prerelease versions are included when include_prereleases is set."""
        registry_data = {
            'dist-tags': {'latest': '10.0.0'},
            'versions': {'9.0.0': {}, '10.0.0': {}, '11.0.0-beta.1': {}},
        }
        response = MagicMock()
        response.json.return_value = registry_data
        response.raise_for_status = MagicMock()

        with patch('porringer.core.plugin_schema.environment.aiohttp.ClientSession') as mock_client:
            _setup_async_client(mock_client, response)
            result = await Environment._check_npm_registry(
                [PackageRef.model_validate('typescript')],
                include_prereleases=True,
            )

        assert len(result) == 1
        assert result[0].version == '11.0.0-beta.1'

    @staticmethod
    async def test_http_error_returns_empty() -> None:
        """HTTP errors result in an empty list."""
        with patch('porringer.core.plugin_schema.environment.aiohttp.ClientSession') as mock_client:
            mock_instance = MagicMock()
            error_resp = MagicMock()
            error_resp.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError('timeout'))
            error_resp.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = MagicMock(return_value=error_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await Environment._check_npm_registry([PackageRef.model_validate('typescript')])

        assert result == []
