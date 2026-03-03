"""Tests for the package uninstall feature.

Covers:
- resolve_uninstall_operation (presence → UNINSTALL or SKIP/NOT_INSTALLED)
- execute_uninstall (routing to environment.uninstall / plugin_manager.plugin_remove)
- get_uninstall_cli_command (preview command generation)
- SkipReason.NOT_INSTALLED enum value
- uninstall_command on MockEnvironment
- plugin_remove_command / plugin_remove on MockPluginManager
- API.uninstall() auto-resolves runtime_context
"""

import asyncio
from pathlib import Path
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.version import Version

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core.action_builder import get_uninstall_cli_command
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import execute_uninstall
from porringer.backend.command.core.resolution import (
    ResolutionContext,
    resolve_uninstall_operation,
    resolved_to_result,
)
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext, RuntimeProvider
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import LocalConfiguration, SetupAction, Skip, SkipReason, Uninstall
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
        assert isinstance(resolved.operation, Uninstall)
        assert resolved.operation.installed_version == '2.31.0'

    @staticmethod
    async def test_not_installed_package_returns_skip() -> None:
        """Package is not installed → SKIP with NOT_INSTALLED."""
        action = _make_action()
        env = _make_mock_env(installed=[])
        envs: dict[str, Environment] = {'mock': env}

        resolved = await resolve_uninstall_operation(action, envs)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.NOT_INSTALLED

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
        assert isinstance(resolved.operation, Skip)

    @staticmethod
    async def test_unavailable_installer_returns_skip() -> None:
        """Installer not in environments dict → SKIP."""
        action = _make_action(installer='nonexistent')
        resolved = await resolve_uninstall_operation(action, {})
        assert isinstance(resolved.operation, Skip)

    @staticmethod
    async def test_plugin_target_installed_returns_uninstall() -> None:
        """Plugin-target: plugin installed → UNINSTALL."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_uninstall_operation(action, {}, ResolutionContext(project_environments=proj_envs))
        assert isinstance(resolved.operation, Uninstall)
        assert resolved.plugin_manager is mock_pm

    @staticmethod
    async def test_plugin_target_not_installed_returns_skip() -> None:
        """Plugin-target: plugin not installed → SKIP/NOT_INSTALLED."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_uninstall_operation(action, {}, ResolutionContext(project_environments=proj_envs))
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.NOT_INSTALLED

    @staticmethod
    async def test_plugin_target_no_manager_returns_skip() -> None:
        """Plugin-target: no PluginManager found → SKIP."""
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')

        resolved = await resolve_uninstall_operation(action, {})
        assert isinstance(resolved.operation, Skip)

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
        result = await execute_uninstall(action, envs, asyncio.Queue())
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
        result = await execute_uninstall(action, envs, asyncio.Queue())
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
        result = await execute_uninstall(action, {}, asyncio.Queue())
        assert result.success is False

    @staticmethod
    async def test_skips_when_installer_not_available() -> None:
        """execute_uninstall skips when installer is not in environments."""
        action = _make_action(installer='nonexistent')
        result = await execute_uninstall(action, {}, asyncio.Queue())
        # Should skip because the installer isn't available
        assert result.skipped is True

    @staticmethod
    async def test_plugin_target_routes_to_plugin_remove() -> None:
        """execute_uninstall routes plugin-target to plugin_remove."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = _make_action(package='cppython', installer='pipx', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=proj_envs)

        result = await execute_uninstall(action, {}, asyncio.Queue(), context)
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

        result = await execute_uninstall(action, {}, asyncio.Queue(), context)
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


# ---------------------------------------------------------------------------
# API.uninstall — runtime context auto-resolution
# ---------------------------------------------------------------------------


class TestUninstallAutoResolveRuntimeContext:
    """API.uninstall() auto-resolves RuntimeContext when none is provided.

    This ensures that RuntimeConsumer plugins (e.g. pip) can
    locate and remove packages using the correct interpreter even
    when the caller does not supply a runtime context.
    """

    @staticmethod
    async def test_auto_resolves_when_none() -> None:
        """When runtime_context=None, Builder.resolve_runtime_context is called."""
        ctx = RuntimeContext(executables={'python': Path('/fake/python')})
        mock_env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])

        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx) as mock_resolve,
            patch.object(type(mock_env), 'ecosystem', return_value=_PY),
            patch.object(type(mock_env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.uninstall('mock', PackageRef.model_validate('requests'), dry_run=True)

        mock_resolve.assert_called_once()
        assert result is not None

    @staticmethod
    async def test_explicit_context_skips_auto_resolve() -> None:
        """When runtime_context is provided, Builder.resolve_runtime_context is NOT called."""
        ctx = RuntimeContext(executables={'python': Path('/explicit/python')})
        mock_env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])

        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
            patch.object(type(mock_env), 'ecosystem', return_value=_PY),
            patch.object(type(mock_env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.uninstall(
                'mock',
                PackageRef.model_validate('requests'),
                runtime_context=ctx,
                dry_run=True,
            )

        mock_resolve.assert_not_called()
        assert result is not None

    @staticmethod
    async def test_empty_managed_but_resolvable_tags() -> None:
        """Runtime resolves even when packages() is empty but available_tags() succeeds.

        Reproduces the bug where PIM's packages() returns [] (no
        pymanager-managed runtimes) but the ``py`` launcher can still
        dispatch to a Python installed via python.org or the Microsoft
        Store.  After the fix, Builder.resolve_runtime_context() uses
        available_tags() instead of packages(), so the runtime is
        resolved and the uninstall targets the correct interpreter.
        """
        resolved_python = Path('/python/3.14/python')

        # Build a concrete RuntimeProvider where packages() is empty but
        # available_tags() and resolve_executable() succeed — mimics a
        # non-pymanager Python visible to the ``py`` launcher.
        class _NonManagedProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

            @staticmethod
            def ecosystem() -> Ecosystem:
                return _PY

            @staticmethod
            def plugin_kind() -> PluginKind:
                return PluginKind.RUNTIME

            @classmethod
            def provided_runtime_kind(cls) -> str:
                return 'python'

            @classmethod
            def tool_name(cls) -> str:
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                if tag == '3.14':
                    return resolved_python
                return None

            @override
            async def available_tags(self) -> list[str]:
                # Non-managed runtimes visible via ``py list``
                return ['3.14']

            @override
            async def packages(self, **kw) -> list[Package]:
                # ``py list --only-managed`` returns empty
                return []

            @override
            async def check_updates(self, params):
                return []

            @override
            def install_command(self, package, **kw):
                return []

            @override
            def upgrade_command(self, package, **kw):
                return []

            @override
            def uninstall_command(self, package, **kw):
                return []

            @staticmethod
            def dependencies() -> list:
                return []

            @property
            def distribution(self) -> Distribution:
                return self._distribution

        provider = _NonManagedProvider(_MOCK_PARAMS)
        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert ctx.executables.get('python') == resolved_python, (
            'Builder.resolve_runtime_context should populate python from available_tags() even when packages() is empty'
        )

        # Now verify the uninstall flow uses this non-empty context
        mock_env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
            runtime_context=ctx,
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(type(mock_env), 'ecosystem', return_value=_PY),
            patch.object(type(mock_env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.uninstall(
                'mock',
                PackageRef.model_validate('requests'),
                plugins=plugins,
                dry_run=True,
            )

        assert result is not None


# ---------------------------------------------------------------------------
# API.resolve_runtime_context
# ---------------------------------------------------------------------------


class TestAPIResolveRuntimeContext:
    """Verify the deprecated API.resolve_runtime_context() helper."""

    @staticmethod
    async def test_auto_discovers_when_no_environments() -> None:
        """When environments=None, plugins are auto-discovered."""
        ctx = RuntimeContext(executables={'python': Path('/resolved/python')})
        mock_env = _make_mock_env()

        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins) as mock_discover,
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx) as mock_resolve,
            pytest.warns(DeprecationWarning, match='resolve_runtime_context.*deprecated'),
        ):
            result = await API.resolve_runtime_context()

        mock_discover.assert_called_once_with(use_cache=True)
        mock_resolve.assert_called_once_with({'mock': mock_env})
        assert result is ctx

    @staticmethod
    async def test_uses_provided_environments() -> None:
        """When environments dict is passed, discovery is skipped."""
        ctx = RuntimeContext(executables={'python': Path('/resolved/python')})
        env_dict: dict[str, Environment] = {'pip': MagicMock(spec=Environment)}

        with (
            patch('porringer.api.discover_all_plugins') as mock_discover,
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx) as mock_resolve,
            pytest.warns(DeprecationWarning, match='resolve_runtime_context.*deprecated'),
        ):
            result = await API.resolve_runtime_context(environments=env_dict)

        mock_discover.assert_not_called()
        mock_resolve.assert_called_once_with(env_dict)
        assert result is ctx
