"""Tests for the PluginManager protocol and native plugin management routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from packaging.version import Version

from porringer.backend.command.action_builder import get_cli_command
from porringer.backend.command.execution import PluginContext, execute_package
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.plugin.pdm.plugin import PdmProjectEnvironment
from porringer.plugin.poetry.plugin import PoetryProjectEnvironment
from porringer.schema import (
    SetupAction,
    SyncStrategy,
)

_PY = Ecosystem('python')
_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestPluginManagerProtocol:
    """Verify that concrete plugins satisfy the PluginManager protocol."""

    @staticmethod
    def test_pdm_is_plugin_manager() -> None:
        """PdmProjectEnvironment implements PluginManager."""
        assert issubclass(PdmProjectEnvironment, PluginManager)

    @staticmethod
    def test_poetry_is_plugin_manager() -> None:
        """PoetryProjectEnvironment implements PluginManager."""
        assert issubclass(PoetryProjectEnvironment, PluginManager)

    @staticmethod
    def test_pdm_isinstance_check() -> None:
        """isinstance() check works for PdmProjectEnvironment."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        assert isinstance(plugin, PluginManager)

    @staticmethod
    def test_poetry_isinstance_check() -> None:
        """isinstance() check works for PoetryProjectEnvironment."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        assert isinstance(plugin, PluginManager)


# ---------------------------------------------------------------------------
# plugin_add_command
# ---------------------------------------------------------------------------


class TestPluginAddCommand:
    """Test the plugin_add_command implementations."""

    @staticmethod
    def test_pdm_plugin_add_command_bare() -> None:
        """PDM plugin_add_command for a bare package name."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        assert plugin.plugin_add_command(ref) == ['pdm', 'self', 'add', 'cppython']

    @staticmethod
    def test_pdm_plugin_add_command_with_constraint() -> None:
        """PDM plugin_add_command includes version constraint."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython>=0.5')
        assert plugin.plugin_add_command(ref) == ['pdm', 'self', 'add', 'cppython>=0.5']

    @staticmethod
    def test_poetry_plugin_add_command_bare() -> None:
        """Poetry plugin_add_command for a bare package name."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export')
        assert plugin.plugin_add_command(ref) == ['poetry', 'self', 'add', 'poetry-plugin-export']

    @staticmethod
    def test_poetry_plugin_add_command_with_constraint() -> None:
        """Poetry plugin_add_command includes version constraint."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export>=1.0,<2.0')
        cmd = plugin.plugin_add_command(ref)
        assert cmd[0:3] == ['poetry', 'self', 'add']
        assert 'poetry-plugin-export' in cmd[3]
        assert '>=1.0' in cmd[3]


# ---------------------------------------------------------------------------
# async_plugin_add
# ---------------------------------------------------------------------------


class TestAsyncPluginAdd:
    """Test the async_plugin_add default implementation."""

    @staticmethod
    def test_async_plugin_add_success() -> None:
        """async_plugin_add returns Package on success."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Added cppython'
        mock_result.stderr = ''

        async def _run() -> Package | None:
            with patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd:
                mock_cmd.return_value = mock_result
                return await plugin.async_plugin_add(params)

        result = asyncio.run(_run())
        assert result is not None
        assert result.name == 'cppython'

    @staticmethod
    def test_async_plugin_add_failure() -> None:
        """async_plugin_add returns None on non-zero exit."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Error: package not found'

        async def _run() -> Package | None:
            with patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd:
                mock_cmd.return_value = mock_result
                return await plugin.async_plugin_add(params)

        result = asyncio.run(_run())
        assert result is None

    @staticmethod
    def test_async_plugin_add_file_not_found() -> None:
        """async_plugin_add returns None when tool is not on PATH."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        async def _run() -> Package | None:
            with patch(
                'porringer.core.plugin_schema.plugin_manager.run_command',
                new_callable=AsyncMock,
                side_effect=FileNotFoundError,
            ):
                return await plugin.async_plugin_add(params)

        result = asyncio.run(_run())
        assert result is None


# ---------------------------------------------------------------------------
# CLI command preview routing
# ---------------------------------------------------------------------------


class TestCliCommandPreview:
    """Test that get_cli_command prefers native plugin management."""

    @staticmethod
    def _make_pdm_project_env() -> PdmProjectEnvironment:
        return PdmProjectEnvironment(_MOCK_PARAMS)

    def test_native_command_when_plugin_manager_available(self) -> None:
        """get_cli_command returns native command when PluginManager is on PATH."""
        pdm_env = self._make_pdm_project_env()
        project_environments: dict[str, ProjectEnvironment] = {'pdmproject': pdm_env}

        action = SetupAction(
            description="Add 'cppython' to 'pdm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('pdm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        with patch.object(type(pdm_env), 'is_available', return_value=True):
            cmd = get_cli_command(action, environments, SyncStrategy.MINIMAL, project_environments)

        assert cmd == ['pdm', 'self', 'add', 'cppython']

    def test_empty_command_when_plugin_manager_unavailable(self) -> None:
        """get_cli_command returns empty list when PluginManager tool is not on PATH."""
        pdm_env = self._make_pdm_project_env()
        project_environments: dict[str, ProjectEnvironment] = {'pdmproject': pdm_env}

        action = SetupAction(
            description="Add 'cppython' to 'pdm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('pdm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        with patch.object(type(pdm_env), 'is_available', return_value=False):
            cmd = get_cli_command(action, environments, SyncStrategy.MINIMAL, project_environments)

        assert cmd == []

    def test_empty_command_when_no_project_environments(self) -> None:
        """get_cli_command returns empty list with no project envs."""
        action = SetupAction(
            description="Add 'cppython' to 'pdm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('pdm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        cmd = get_cli_command(action, environments, SyncStrategy.MINIMAL, None)
        assert cmd == []


# ---------------------------------------------------------------------------
# Execution routing
# ---------------------------------------------------------------------------


class TestPluginAddRouting:
    """Test that execute_package routes to PluginManager for plugin actions."""

    @staticmethod
    def test_routes_to_native_when_plugin_manager_available() -> None:
        """execute_package uses PluginManager for plugin_target actions."""
        pdm_env = PdmProjectEnvironment(_MOCK_PARAMS)

        action = SetupAction(
            description="Add 'cppython' to 'pdm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('pdm'),
        )

        project_environments: dict[str, ProjectEnvironment] = {'pdmproject': pdm_env}
        context = PluginContext(project_environments=project_environments)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Added cppython'
        mock_result.stderr = ''

        async def _run():
            with (
                patch.object(type(pdm_env), 'is_available', return_value=True),
                patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd,
            ):
                mock_cmd.return_value = mock_result
                return await execute_package(action, {}, SyncStrategy.MINIMAL, None, context)

        result = asyncio.run(_run())
        assert result.success is True
        assert result.message is not None
        assert 'native' in result.message.lower()

    @staticmethod
    def test_fails_when_no_plugin_manager() -> None:
        """execute_package fails when no PluginManager is found for plugin_target."""
        action = SetupAction(
            description="Add 'cppython' to 'pdm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('pdm'),
        )

        async def _run():
            return await execute_package(action, {}, SyncStrategy.MINIMAL)

        result = asyncio.run(_run())
        assert result.success is False
        assert result.message is not None
        assert 'No PluginManager found' in result.message
