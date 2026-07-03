"""Helpers for test plugin manager.

Tests for the PluginManager protocol and native plugin management routing.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.version import Version

from porringer.backend.command.core.action_builder import get_cli_command
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import execute_package
from porringer.backend.command.core.resolution import (
    ResolutionContext,
)
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectInstaller
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    PluginParameters,
)
from porringer.plugin.pdm.plugin import PDMEnvironment
from porringer.plugin.poetry.plugin import PoetryEnvironment
from porringer.schema import (
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


class TestPluginManagerProtocol:
    """Verify that concrete plugins satisfy the PluginManager protocol."""

    @staticmethod
    @pytest.mark.parametrize(
        'plugin_factory',
        [
            pytest.param(PDMEnvironment, id='pdm'),
            pytest.param(PoetryEnvironment, id='poetry'),
        ],
    )
    def test_isinstance_check(plugin_factory: type[PDMEnvironment | PoetryEnvironment]) -> None:
        """isinstance() check works for the concrete plugin."""
        plugin = plugin_factory(_MOCK_PARAMS)
        assert isinstance(plugin, PluginManager)


# ---------------------------------------------------------------------------
# plugin_install_command
# ---------------------------------------------------------------------------


class TestPluginInstallCommand:
    """Test the plugin_install_command implementations."""

    @staticmethod
    @pytest.mark.parametrize(
        ('plugin_factory', 'ref_string'),
        [
            pytest.param(PDMEnvironment, 'cppython', id='pdm-bare'),
            pytest.param(PDMEnvironment, 'cppython>=0.5', id='pdm-constraint'),
            pytest.param(PoetryEnvironment, 'poetry-plugin-export', id='poetry-bare'),
            pytest.param(PoetryEnvironment, 'poetry-plugin-export>=1.0,<2.0', id='poetry-constraint'),
        ],
    )
    def test_plugin_install_command(plugin_factory: type[PDMEnvironment | PoetryEnvironment], ref_string: str) -> None:
        """plugin_install_command starts with tool_name and includes the specifier."""
        plugin = plugin_factory(_MOCK_PARAMS)
        ref = PackageRef.model_validate(ref_string)
        cmd = plugin.plugin_install_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd


# ---------------------------------------------------------------------------
# plugin_install
# ---------------------------------------------------------------------------


class TestAsyncPluginInstall:
    """Test the plugin_install default implementation."""

    @staticmethod
    async def test_async_plugin_install_success() -> None:
        """plugin_install returns Package on success."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Added cppython'
        mock_result.stderr = ''

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
        ) as mock_cmd:
            mock_cmd.return_value = mock_result
            result = await plugin.plugin_install(params)
        assert result is not None
        assert result.name == 'cppython'

    @staticmethod
    async def test_async_plugin_install_failure() -> None:
        """plugin_install returns None on non-zero exit."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Error: package not found'

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
        ) as mock_cmd:
            mock_cmd.return_value = mock_result
            result = await plugin.plugin_install(params)
        assert result is None

    @staticmethod
    async def test_async_plugin_install_file_not_found() -> None:
        """plugin_install returns None when tool is not on PATH."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            result = await plugin.plugin_install(params)
        assert result is None


# ---------------------------------------------------------------------------
# CLI command preview routing
# ---------------------------------------------------------------------------


class TestCliCommandPreview:
    """Test that get_cli_command prefers native plugin management."""

    @staticmethod
    def _make_mock_pm(*, installed: list[Package] | None = None) -> MockPluginManager:
        return MockPluginManager(_MOCK_PARAMS, installed=installed)

    def test_native_command_when_plugin_manager_available(self) -> None:
        """get_cli_command returns native command when PluginManager is on PATH."""
        mock_pm = self._make_mock_pm()
        project_environments: dict[str, ProjectInstaller] = {'mockpmproject': mock_pm}
        ref = PackageRef.model_validate('cppython')

        action = setup_action('cppython', target='mock-pm')
        environments: dict[str, Environment] = {'pipx': make_environment()}

        cmd = get_cli_command(
            action,
            _make_plugins(environments, project_environments),
            SyncStrategy.MINIMAL,
        )
        assert cmd == tuple(mock_pm.plugin_install_command(ref))

    @staticmethod
    def test_empty_command_when_plugin_manager_unavailable() -> None:
        """get_cli_command returns empty tuple when PluginManager tool is not on PATH."""
        pdm_env = PDMEnvironment(_MOCK_PARAMS)
        project_environments: dict[str, ProjectInstaller] = {'pdmproject': pdm_env}

        action = setup_action('cppython', target='pdm')
        environments: dict[str, Environment] = {'pipx': make_environment()}

        with patch.object(type(pdm_env), 'is_available', return_value=False):
            cmd = get_cli_command(
                action,
                _make_plugins(environments, project_environments),
                SyncStrategy.MINIMAL,
            )

        assert cmd == ()

    @staticmethod
    def test_empty_command_when_no_project_environments() -> None:
        """get_cli_command returns empty tuple with no project envs."""
        action = setup_action('cppython', target='pdm')
        environments: dict[str, Environment] = {'pipx': make_environment()}

        cmd = get_cli_command(action, _make_plugins(environments), SyncStrategy.MINIMAL)
        assert cmd == ()


# ---------------------------------------------------------------------------
# Execution routing
# ---------------------------------------------------------------------------


class TestPluginInstallRouting:
    """Test that execute_package routes to PluginManager for plugin actions."""

    @staticmethod
    async def test_routes_to_native_when_plugin_manager_available() -> None:
        """execute_package uses PluginManager for plugin_target actions."""
        mock_pm = MockPluginManager(_MOCK_PARAMS)

        action = setup_action('cppython', target='mock-pm')

        project_environments: dict[str, ProjectInstaller] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(action, {}, SyncStrategy.MINIMAL, asyncio.Queue(), context)
        assert result.success is True
        assert result.message is not None
        assert 'native' in result.message.lower()
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'install'

    @staticmethod
    async def test_fails_when_no_plugin_manager() -> None:
        """execute_package fails when no PluginManager is found for plugin_target."""
        action = setup_action('cppython', target='pdm')

        result = await execute_package(action, {}, SyncStrategy.MINIMAL, asyncio.Queue())
        assert result.success is False
        assert result.message is not None
        assert 'No PluginManager found' in result.message


# ---------------------------------------------------------------------------
# Plugin list / query
# ---------------------------------------------------------------------------
