"""Tests for the package uninstall feature.

Covers:
- resolve_uninstall_operation (presence → UNINSTALL or SKIP/NOT_INSTALLED)
- execute_uninstall (routing to environment.uninstall / plugin_manager.plugin_remove)
- get_uninstall_cli_command (preview command generation)
- SkipReason.NOT_INSTALLED enum value
- uninstall_command on MockEnvironment
- plugin_remove_command / plugin_remove on MockPluginManager
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from packaging.version import Version

from porringer.backend.command.core.action_builder import get_uninstall_cli_command
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import execute_uninstall
from porringer.backend.command.core.resolution import (
    OperationKind,
    ResolutionContext,
    resolve_uninstall_operation,
    resolved_to_result,
)
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import SetupAction, SkipReason
from porringer.test.mock.environment import MockEnvironment
from porringer.test.mock.plugin_manager import MockPluginManager

_PY = Ecosystem('python')
_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


def _make_plugins(
    environments: dict[str, Environment] | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> DiscoveredPlugins:
    return DiscoveredPlugins(
        environments=environments or {},
        project_environments=project_environments or {},
        scm_environments={},
    )


def _make_action(
    *,
    package: str = 'requests',
    installer: str = 'mock',
    plugin_target: str | None = None,
) -> SetupAction:
    return SetupAction(
        description=f"Uninstall '{package}'",
        kind=PluginKind.PACKAGE,
        ecosystem=_PY,
        installer=installer,
        package=PackageRef.model_validate(package),
        plugin_target=PackageRef.model_validate(plugin_target) if plugin_target else None,
    )


def _make_mock_env(*, installed: list[Package] | None = None) -> MockEnvironment:
    """Create a MockEnvironment with configurable packages()."""
    env = MockEnvironment(_MOCK_PARAMS)
    pkgs = installed or []

    async def _packages(*, project_path: Path | None = None, runtime_context: object = None) -> list[Package]:
        del project_path, runtime_context
        return pkgs

    env.packages = _packages  # type: ignore[assignment]
    return env


# ---------------------------------------------------------------------------
# SkipReason.NOT_INSTALLED
# ---------------------------------------------------------------------------


class TestNotInstalledSkipReason:
    """Verify the NOT_INSTALLED enum value exists and behaves correctly."""

    @staticmethod
    def test_not_installed_is_skip_reason() -> None:
        """NOT_INSTALLED exists and is distinct from ALREADY_INSTALLED."""
        assert SkipReason.NOT_INSTALLED != SkipReason.ALREADY_INSTALLED


# ---------------------------------------------------------------------------
# MockEnvironment.uninstall_command
# ---------------------------------------------------------------------------


class TestMockEnvironmentUninstallCommand:
    """Verify MockEnvironment returns a well-formed uninstall command."""

    @staticmethod
    def test_returns_list_of_strings() -> None:
        """uninstall_command returns a list of strings."""
        env = MockEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('requests')
        cmd = env.uninstall_command(ref)
        assert isinstance(cmd, list)
        assert all(isinstance(part, str) for part in cmd)

    @staticmethod
    def test_contains_package_name() -> None:
        """uninstall_command includes the package name."""
        env = MockEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('requests')
        cmd = env.uninstall_command(ref)
        assert 'requests' in cmd

    @staticmethod
    def test_contains_uninstall_verb() -> None:
        """uninstall_command includes the 'uninstall' verb."""
        env = MockEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('requests')
        cmd = env.uninstall_command(ref)
        assert 'uninstall' in cmd


# ---------------------------------------------------------------------------
# MockPluginManager.plugin_remove_command / plugin_remove
# ---------------------------------------------------------------------------


class TestMockPluginManagerRemove:
    """Verify MockPluginManager remove operations."""

    @staticmethod
    def test_plugin_remove_command_returns_list() -> None:
        """plugin_remove_command returns a list containing 'remove'."""
        pm = MockPluginManager(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        cmd = pm.plugin_remove_command(ref)
        assert isinstance(cmd, list)
        assert 'remove' in cmd
        assert 'cppython' in cmd

    @staticmethod
    async def test_async_plugin_remove_records_operation() -> None:
        """plugin_remove records a ('remove', ref) operation."""
        pm = MockPluginManager(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)
        result = await pm.plugin_remove(params)
        assert result is not None
        assert result.name == 'cppython'
        assert len(pm.operations) == 1
        assert pm.operations[0] == ('remove', ref)


# ---------------------------------------------------------------------------
# resolve_uninstall_operation
# ---------------------------------------------------------------------------


class TestResolveUninstallOperation:
    """Test that resolve_uninstall_operation correctly determines UNINSTALL vs SKIP."""

    @staticmethod
    async def test_installed_package_returns_uninstall() -> None:
        """Package is installed → UNINSTALL."""
        action = _make_action()
        env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        envs: dict[str, Environment] = {'mock': env}

        resolved = await resolve_uninstall_operation(action, envs)
        assert resolved.operation == OperationKind.UNINSTALL
        assert resolved.installed_version == '2.31.0'

    @staticmethod
    async def test_not_installed_package_returns_skip() -> None:
        """Package is not installed → SKIP with NOT_INSTALLED."""
        action = _make_action()
        env = _make_mock_env(installed=[])
        envs: dict[str, Environment] = {'mock': env}

        resolved = await resolve_uninstall_operation(action, envs)
        assert resolved.operation == OperationKind.SKIP
        assert resolved.skip_reason == SkipReason.NOT_INSTALLED

    @staticmethod
    async def test_missing_installer_returns_skip() -> None:
        """Action with no installer → SKIP."""
        action = SetupAction(
            description='Bad action',
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=None,
            package=None,
        )
        resolved = await resolve_uninstall_operation(action, {})
        assert resolved.operation == OperationKind.SKIP

    @staticmethod
    async def test_unavailable_installer_returns_skip() -> None:
        """Installer not in environments dict → SKIP."""
        action = _make_action(installer='nonexistent')
        resolved = await resolve_uninstall_operation(action, {})
        assert resolved.operation == OperationKind.SKIP

    @staticmethod
    async def test_plugin_target_installed_returns_uninstall() -> None:
        """Plugin-target: plugin installed → UNINSTALL."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_uninstall_operation(action, {}, ResolutionContext(project_environments=proj_envs))
        assert resolved.operation == OperationKind.UNINSTALL
        assert resolved.plugin_manager is mock_pm

    @staticmethod
    async def test_plugin_target_not_installed_returns_skip() -> None:
        """Plugin-target: plugin not installed → SKIP/NOT_INSTALLED."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_uninstall_operation(action, {}, ResolutionContext(project_environments=proj_envs))
        assert resolved.operation == OperationKind.SKIP
        assert resolved.skip_reason == SkipReason.NOT_INSTALLED

    @staticmethod
    async def test_plugin_target_no_manager_returns_skip() -> None:
        """Plugin-target: no PluginManager found → SKIP."""
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')

        resolved = await resolve_uninstall_operation(action, {})
        assert resolved.operation == OperationKind.SKIP

    @staticmethod
    async def test_resolved_to_result_for_skip() -> None:
        """resolved_to_result maps SKIP/NOT_INSTALLED correctly."""
        action = _make_action()
        env = _make_mock_env(installed=[])
        envs: dict[str, Environment] = {'mock': env}

        resolved = await resolve_uninstall_operation(action, envs)
        result = resolved_to_result(resolved)
        assert result.skipped is True
        assert result.skip_reason == SkipReason.NOT_INSTALLED


# ---------------------------------------------------------------------------
# execute_uninstall
# ---------------------------------------------------------------------------


class TestExecuteUninstall:
    """Test that execute_uninstall routes to the correct async method."""

    @staticmethod
    async def test_uninstalls_installed_package() -> None:
        """execute_uninstall calls uninstall on installed package."""
        env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        # Patch uninstall to verify it's called
        env.uninstall = AsyncMock(return_value=Package(name='requests', version=None))  # type: ignore[assignment]
        envs: dict[str, Environment] = {'mock': env}

        action = _make_action()
        result = await execute_uninstall(action, envs)
        assert result.success is True
        assert result.message is not None
        assert 'Uninstalled' in result.message
        env.uninstall.assert_awaited_once()

    @staticmethod
    async def test_skips_not_installed_package() -> None:
        """execute_uninstall skips when package is not installed."""
        env = _make_mock_env(installed=[])
        envs: dict[str, Environment] = {'mock': env}

        action = _make_action()
        result = await execute_uninstall(action, envs)
        assert result.skipped is True
        assert result.skip_reason == SkipReason.NOT_INSTALLED

    @staticmethod
    async def test_fails_when_installer_missing() -> None:
        """execute_uninstall fails when installer is not specified."""
        action = SetupAction(
            description='Bad action',
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=None,
            package=None,
        )
        result = await execute_uninstall(action, {})
        assert result.success is False

    @staticmethod
    async def test_skips_when_installer_not_available() -> None:
        """execute_uninstall skips when installer is not in environments."""
        action = _make_action(installer='nonexistent')
        result = await execute_uninstall(action, {})
        # Should skip because the installer isn't available
        assert result.skipped is True

    @staticmethod
    async def test_plugin_target_routes_to_plugin_remove() -> None:
        """execute_uninstall routes plugin-target to plugin_remove."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=proj_envs)

        result = await execute_uninstall(action, {}, None, context)
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'remove'

    @staticmethod
    async def test_plugin_target_skips_when_not_installed() -> None:
        """execute_uninstall skips when plugin is not installed."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=proj_envs)

        result = await execute_uninstall(action, {}, None, context)
        assert result.skipped is True
        assert result.skip_reason == SkipReason.NOT_INSTALLED
        assert len(mock_pm.operations) == 0


# ---------------------------------------------------------------------------
# get_uninstall_cli_command
# ---------------------------------------------------------------------------


class TestUninstallCliCommand:
    """Test CLI command preview for uninstall actions."""

    @staticmethod
    def test_returns_environment_uninstall_command() -> None:
        """get_uninstall_cli_command returns the plugin's uninstall_command."""
        env = MockEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('requests')
        action = SetupAction(
            description="Uninstall 'requests'",
            kind=PluginKind.PACKAGE,
            ecosystem=_PY,
            installer='mock',
            package=ref,
        )
        plugins = _make_plugins(environments={'mock': env})
        cmd = get_uninstall_cli_command(action, plugins)
        assert cmd == env.uninstall_command(ref)

    @staticmethod
    def test_returns_empty_for_unknown_installer() -> None:
        """get_uninstall_cli_command returns empty list when installer not found."""
        action = SetupAction(
            description="Uninstall 'requests'",
            kind=PluginKind.PACKAGE,
            ecosystem=_PY,
            installer='nonexistent',
            package=PackageRef.model_validate('requests'),
        )
        plugins = _make_plugins()
        cmd = get_uninstall_cli_command(action, plugins)
        assert cmd == []

    @staticmethod
    def test_plugin_target_returns_remove_command() -> None:
        """get_uninstall_cli_command returns plugin_remove_command for plugin-target."""
        mock_pm = MockPluginManager(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        action = SetupAction(
            description="Remove 'cppython' from 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('mock-pm'),
        )
        mock_env = MagicMock(spec=Environment)
        plugins = _make_plugins(
            environments={'pipx': mock_env},
            project_environments={'mockpmproject': mock_pm},
        )
        cmd = get_uninstall_cli_command(action, plugins)
        assert cmd == mock_pm.plugin_remove_command(ref)

    @staticmethod
    def test_returns_empty_for_project_kind() -> None:
        """get_uninstall_cli_command returns empty for PROJECT kind."""
        action = SetupAction(
            description='Sync project',
            kind=PluginKind.PROJECT,
            ecosystem=_PY,
            installer='pdm',
        )
        plugins = _make_plugins()
        cmd = get_uninstall_cli_command(action, plugins)
        assert cmd == []
