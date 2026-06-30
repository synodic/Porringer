"""Helpers for test plugin manager presence.

Tests for plugin listing, parsing, and inspection-time presence checks.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import execute_package
from porringer.backend.command.core.presence import inspect_action
from porringer.backend.command.core.resolution import (
    ResolutionContext,
)
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectInstaller
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PluginParameters,
)
from porringer.plugin.pdm.plugin import PDMEnvironment
from porringer.plugin.poetry.plugin import PoetryEnvironment
from porringer.schema import (
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from porringer.test.mock.plugin_manager import MockPluginManager
from tests.fixtures.factories import make_environment, setup_action

_PY = Ecosystem('python')
_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


def _make_plugins(
    environments: dict[str, Environment] | None = None,
    project_environments: dict[str, ProjectInstaller] | None = None,
) -> DiscoveredPlugins:
    """Build a ``DiscoveredPlugins`` container for test helpers."""
    return DiscoveredPlugins(
        environments=environments or {},
        project_environments=project_environments or {},
        scm_environments={},
    )


class TestPluginListCommand:
    """Test the plugin_list_command implementations."""

    @staticmethod
    @pytest.mark.parametrize(
        'plugin_factory',
        [
            pytest.param(PDMEnvironment, id='pdm'),
            pytest.param(PoetryEnvironment, id='poetry'),
        ],
    )
    def test_plugin_list_command(plugin_factory: type[PDMEnvironment | PoetryEnvironment]) -> None:
        """plugin_list_command starts with tool name."""
        plugin = plugin_factory(_MOCK_PARAMS)
        cmd = plugin.plugin_list_command()
        assert cmd[0] == plugin.tool_name()
        assert len(cmd) > 1


class TestParsePluginList:
    """Test plugin list output parsing."""

    @staticmethod
    def test_pdm_parse_with_version_and_description() -> None:
        """PDM parser extracts name and version from tabular output."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        stdout = 'cppython  0.9.14  A Python management solution for C++\n'
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 1
        assert result[0].name == 'cppython'
        assert result[0].version == '0.9.14'

    @staticmethod
    def test_pdm_parse_multiple_plugins() -> None:
        """PDM parser handles multiple lines."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        stdout = 'cppython 0.9.14 desc\npoetry-plugin 1.0.0 other\n'
        result = plugin.parse_plugin_list(stdout)
        expected_plugin_count = 2
        assert len(result) == expected_plugin_count
        assert result[0].name == 'cppython'
        assert result[1].name == 'poetry-plugin'

    @staticmethod
    def test_pdm_parse_empty_output() -> None:
        """PDM parser returns empty list for empty output."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        assert plugin.parse_plugin_list('') == []
        assert plugin.parse_plugin_list('\n') == []

    @staticmethod
    def test_poetry_parse_plugin_line() -> None:
        """Poetry parser extracts name and version from indented lines."""
        plugin = PoetryEnvironment(_MOCK_PARAMS)
        stdout = '  - poetry-plugin-export (1.6.0) Poetry plugin\n'
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 1
        assert result[0].name == 'poetry-plugin-export'
        assert result[0].version == '1.6.0'

    @staticmethod
    def test_poetry_parse_ignores_non_plugin_lines() -> None:
        """Poetry parser skips header/dependency lines."""
        plugin = PoetryEnvironment(_MOCK_PARAMS)
        stdout = (
            'Installed plugins:\n'
            '  - poetry-plugin-export (1.6.0) Poetry plugin\n'
            '\n'
            '    Dependencies:\n'
            '      - foo (>=1.0)\n'
        )
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 1
        assert result[0].name == 'poetry-plugin-export'


class TestInstalledPlugins:
    """Test the installed_plugins default implementation."""

    @staticmethod
    async def test_installed_plugins_success() -> None:
        """installed_plugins parses subprocess output on success."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'cppython 0.9.14 desc\n', b''))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await plugin.installed_plugins()

        assert len(result) == 1
        assert result[0].name == 'cppython'

    @staticmethod
    async def test_installed_plugins_failure_returns_empty() -> None:
        """installed_plugins returns empty list on non-zero exit."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error'))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await plugin.installed_plugins()

        assert result == []

    @staticmethod
    async def test_installed_plugins_file_not_found() -> None:
        """installed_plugins returns empty list when tool is missing."""
        plugin = PDMEnvironment(_MOCK_PARAMS)

        with patch(
            'asyncio.create_subprocess_exec',
            side_effect=FileNotFoundError,
        ):
            result = await plugin.installed_plugins()

        assert result == []


# ---------------------------------------------------------------------------
# Plugin presence in inspection
# ---------------------------------------------------------------------------

_PLUGIN_ACTION = setup_action('cppython', target='mock-pm')


class TestInspectionPluginPresence:
    """Test that inspect_action correctly queries PluginManager for plugin-target actions."""

    @staticmethod
    def _make_envs(
        *,
        installed: list[Package] | None = None,
    ) -> tuple[MockPluginManager, dict[str, Environment], dict[str, ProjectInstaller]]:
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=installed)
        mock_pipx = make_environment(tool_name='pipx')
        environments: dict[str, Environment] = {'pipx': mock_pipx}
        project_environments: dict[str, ProjectInstaller] = {'mockpmproject': mock_pm}
        return mock_pm, environments, project_environments

    async def test_skips_when_plugin_installed(self) -> None:
        """inspect_action skips plugin-target action when plugin is already installed."""
        _, environments, project_environments = self._make_envs(
            installed=[Package(name='cppython', version='0.9.14')],
        )

        result = await inspect_action(
            _PLUGIN_ACTION,
            environments,
            context=ResolutionContext(project_environments=project_environments),
        )

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    async def test_not_skipped_when_plugin_missing(self) -> None:
        """inspect_action does not skip when plugin is not installed."""
        _, environments, project_environments = self._make_envs(installed=[])

        result = await inspect_action(
            _PLUGIN_ACTION,
            environments,
            context=ResolutionContext(project_environments=project_environments),
        )

        assert result.skipped is not True

    async def test_not_skipped_when_no_plugin_manager(self) -> None:
        """inspect_action does not skip when no PluginManager is available."""
        _, environments, _ = self._make_envs()

        result = await inspect_action(
            _PLUGIN_ACTION,
            environments,
            context=None,
        )

        assert result.skipped is not True

    async def test_upgrade_when_plugin_installed_latest_strategy(self) -> None:
        """inspect_action skips plugin when LATEST strategy and no newer version exists."""
        _, environments, project_environments = self._make_envs(
            installed=[Package(name='cppython', version='0.9.14')],
        )
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(
            _PLUGIN_ACTION,
            environments,
            context=ResolutionContext(project_environments=project_environments),
            parameters=params,
        )

        # LATEST + installed + no newer version -> skip (ALREADY_LATEST)
        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST

    async def test_install_when_plugin_missing_latest_strategy(self) -> None:
        """inspect_action reports install when LATEST strategy and plugin is missing."""
        _, environments, project_environments = self._make_envs(installed=[])
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(
            _PLUGIN_ACTION,
            environments,
            context=ResolutionContext(project_environments=project_environments),
            parameters=params,
        )

        assert result.skipped is not True
        assert result.success is True


# ---------------------------------------------------------------------------
# Plugin presence in execution
# ---------------------------------------------------------------------------


class TestExecutePackagePluginPresence:
    """Test that execute_package skips already-installed plugins."""

    @staticmethod
    async def test_skips_installed_plugin_on_minimal() -> None:
        """execute_package skips plugin-target action when already installed."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        project_environments: dict[str, ProjectInstaller] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, asyncio.Queue(), context)
        assert result.success is True
        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert len(mock_pm.operations) == 0

    @staticmethod
    async def test_installs_missing_plugin_on_minimal() -> None:
        """execute_package installs plugin when not already installed."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectInstaller] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, asyncio.Queue(), context)
        assert result.success is True
        assert result.skipped is not True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'install'


# ---------------------------------------------------------------------------
# plugin_upgrade_command
# ---------------------------------------------------------------------------
