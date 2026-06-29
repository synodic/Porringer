"""Helpers for test plugin manager upgrade.

Tests for plugin upgrade routing, operation resolution, and extras reinstall.
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
    resolve_operation,
)
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    PluginKind,
    PluginParameters,
)
from porringer.plugin.pdm.plugin import PDMEnvironment
from porringer.plugin.poetry.plugin import PoetryEnvironment
from porringer.schema import (
    Install,
    InstallReason,
    SetupAction,
    Skip,
    SkipReason,
    SyncStrategy,
    Upgrade,
)
from porringer.test.mock.plugin_manager import MockPluginManager
from tests.fixtures.factories import make_environment, setup_action

_PY = Ecosystem('python')
_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


def _make_plugins(
    environments: dict[str, Environment] | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> DiscoveredPlugins:
    """Build a ``DiscoveredPlugins`` container for test helpers."""
    return DiscoveredPlugins(
        environments=environments or {},
        project_environments=project_environments or {},
        scm_environments={},
    )


_PLUGIN_ACTION = setup_action('cppython', target='mock-pm')


class TestPluginUpgradeCommand:
    """Test the plugin_upgrade_command implementations."""

    @staticmethod
    @pytest.mark.parametrize(
        'ref_string',
        [
            pytest.param('cppython', id='bare'),
            pytest.param('cppython>=0.5', id='constraint'),
        ],
    )
    def test_pdm_plugin_upgrade_command(ref_string: str) -> None:
        """PDM plugin_upgrade_command starts with tool name, includes the package, differs from install."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate(ref_string)
        cmd = plugin.plugin_upgrade_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd
        # Upgrade must differ from install (install omits the upgrade mechanism)
        assert cmd != plugin.plugin_install_command(ref)

    @staticmethod
    @pytest.mark.parametrize(
        'ref_string',
        [
            pytest.param('poetry-plugin-export', id='bare'),
            pytest.param('poetry-plugin-export>=1.0', id='constraint'),
        ],
    )
    def test_poetry_plugin_upgrade_delegates_to_install(ref_string: str) -> None:
        """Poetry plugin_upgrade_command delegates to plugin_install_command."""
        plugin = PoetryEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate(ref_string)
        upgrade_cmd = plugin.plugin_upgrade_command(ref)
        install_cmd = plugin.plugin_install_command(ref)
        assert upgrade_cmd == install_cmd

    @staticmethod
    def test_pdm_upgrade_includes_pip_upgrade_flag() -> None:
        """PDM upgrade must pass --pip-args=--upgrade so pip actually upgrades.

        Without --pip-args=--upgrade, ``pdm self add <pkg>`` delegates to
        ``pip install <pkg>`` which is a no-op when the package is already
        installed -- pip sees the requirement satisfied and skips the upgrade.
        This test reproduces the bug where ``pdm self add cppython`` reported
        success but left the old version in place.
        """
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')

        install_cmd = plugin.plugin_install_command(ref)
        upgrade_cmd = plugin.plugin_upgrade_command(ref)

        # install intentionally omits the upgrade flag (first install)
        assert not any('--pip-args' in arg for arg in install_cmd)

        # upgrade MUST include --pip-args=--upgrade so pip pulls a newer version
        assert any('--pip-args' in arg and '--upgrade' in arg for arg in upgrade_cmd)

        # The two commands must not be identical
        assert install_cmd != upgrade_cmd


# ---------------------------------------------------------------------------
# plugin_upgrade
# ---------------------------------------------------------------------------


class TestAsyncPluginUpgrade:
    """Test the plugin_upgrade default implementation."""

    @staticmethod
    async def test_async_plugin_upgrade_success() -> None:
        """plugin_upgrade returns Package on success."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Updated cppython'
        mock_result.stderr = ''

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
        ) as mock_cmd:
            mock_cmd.return_value = mock_result
            result = await plugin.plugin_upgrade(params)
        assert result is not None
        assert result.name == 'cppython'

    @staticmethod
    async def test_async_plugin_upgrade_failure() -> None:
        """plugin_upgrade returns None on non-zero exit."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Error: no such plugin'

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
        ) as mock_cmd:
            mock_cmd.return_value = mock_result
            result = await plugin.plugin_upgrade(params)
        assert result is None

    @staticmethod
    async def test_async_plugin_upgrade_file_not_found() -> None:
        """plugin_upgrade returns None when tool is not on PATH."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            result = await plugin.plugin_upgrade(params)
        assert result is None

    @staticmethod
    async def test_async_plugin_upgrade_uses_upgrade_command() -> None:
        """plugin_upgrade delegates to plugin_upgrade_command."""
        plugin = PDMEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Updated cppython'
        mock_result.stderr = ''

        with patch(
            'porringer.core.plugin_schema.plugin_manager.run_command',
            new_callable=AsyncMock,
        ) as mock_cmd:
            mock_cmd.return_value = mock_result
            await plugin.plugin_upgrade(params)
            called_args = mock_cmd.call_args[0][0]
        # The default implementation delegates to plugin_upgrade_command
        assert called_args == plugin.plugin_upgrade_command(ref)


# ---------------------------------------------------------------------------
# resolve_operation -- unified resolution tests
# ---------------------------------------------------------------------------


class TestResolveOperation:
    """Test resolve_operation with different strategies and states."""

    @staticmethod
    def _make_action(
        plugin_target: str | None = None,
        name: str = 'cppython',
        installer: str = 'pipx',
    ) -> SetupAction:
        return setup_action(name, installer=installer, target=plugin_target)

    @staticmethod
    def _make_envs(
        installed: list[Package] | None = None,
    ) -> dict[str, Environment]:
        return {'pipx': make_environment(tool_name='pipx', installed=installed)}

    # --- Normal package resolution ---

    async def test_minimal_installed_skips(self) -> None:
        """MINIMAL + installed -> SKIP."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED

    async def test_minimal_not_installed_installs(self) -> None:
        """MINIMAL + not installed -> INSTALL."""
        action = self._make_action()
        envs = self._make_envs(installed=[])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Install)

    async def test_latest_installed_upgrades(self) -> None:
        """LATEST + installed + no newer version -> SKIP (ALREADY_LATEST)."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_LATEST

    async def test_latest_not_installed_installs(self) -> None:
        """LATEST + not installed -> INSTALL (fallback)."""
        action = self._make_action()
        envs = self._make_envs(installed=[])

        resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Install)

    async def test_exact_installed_upgrades(self) -> None:
        """EXACT + installed + no newer version -> SKIP (ALREADY_LATEST)."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.EXACT)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_LATEST

    # --- Plugin-management resolution ---

    async def test_plugin_minimal_installed_skips(self) -> None:
        """Plugin: MINIMAL + installed -> SKIP."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.MINIMAL,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED
        assert resolved.plugin_manager is mock_pm

    async def test_plugin_minimal_not_installed_installs(self) -> None:
        """Plugin: MINIMAL + not installed -> INSTALL."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.MINIMAL,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Install)
        assert resolved.plugin_manager is mock_pm

    async def test_plugin_latest_installed_upgrades(self) -> None:
        """Plugin: LATEST + installed -> UPGRADE."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.LATEST,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Upgrade)
        assert resolved.plugin_manager is mock_pm

    async def test_plugin_latest_not_installed_installs(self) -> None:
        """Plugin: LATEST + not installed -> INSTALL (fallback)."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.LATEST,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Install)

    async def test_plugin_no_manager_defaults_to_install(self) -> None:
        """Plugin: no PluginManager -> INSTALL."""
        action = self._make_action(plugin_target='mock-pm')

        resolved = await resolve_operation(action, {}, SyncStrategy.MINIMAL)

        assert isinstance(resolved.operation, Install)
        assert resolved.plugin_manager is None

    async def test_skip_returns_installed_version(self) -> None:
        """SKIP result carries installed_version metadata."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='2.3.1')])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.installed_version == '2.3.1'

    @staticmethod
    async def test_missing_installer_skips() -> None:
        """Action with no installer/package -> SKIP."""
        action = SetupAction(
            description='Bad action',
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=None,
            package=None,
        )
        resolved = await resolve_operation(action, {}, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)


# ---------------------------------------------------------------------------
# Plugin upgrade routing in execute_package
# ---------------------------------------------------------------------------


class TestPluginUpgradeRouting:
    """Test that execute_package routes to plugin_upgrade for LATEST strategy."""

    @staticmethod
    async def test_latest_routes_to_upgrade() -> None:
        """execute_package with LATEST calls plugin_upgrade for installed plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.LATEST, asyncio.Queue(), context)
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'upgrade'
        assert mock_pm.operations[0][1].name == 'cppython'

    @staticmethod
    async def test_latest_installs_when_not_present() -> None:
        """execute_package with LATEST calls plugin_install for missing plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.LATEST, asyncio.Queue(), context)
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'install'

    @staticmethod
    async def test_minimal_always_uses_install() -> None:
        """execute_package with MINIMAL uses plugin_install for new plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=project_environments)

        result = await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, asyncio.Queue(), context)
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'install'


# ---------------------------------------------------------------------------
# CLI command preview with upgrade strategies
# ---------------------------------------------------------------------------


class TestCliCommandUpgradePreview:
    """Test that get_cli_command returns upgrade commands for LATEST/EXACT."""

    @staticmethod
    def _make_mock_pm() -> MockPluginManager:
        return MockPluginManager(_MOCK_PARAMS)

    def test_latest_returns_upgrade_command(self) -> None:
        """get_cli_command returns plugin_upgrade_command for LATEST strategy."""
        mock_pm = self._make_mock_pm()
        ref = PackageRef.model_validate('cppython')
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        action = SetupAction(
            description="Upgrade plugin 'cppython' to 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('mock-pm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        cmd = get_cli_command(
            action,
            _make_plugins(environments, project_environments),
            SyncStrategy.LATEST,
        )
        assert cmd == tuple(mock_pm.plugin_upgrade_command(ref))

    def test_minimal_returns_install_command(self) -> None:
        """get_cli_command returns plugin_install_command for MINIMAL strategy."""
        mock_pm = self._make_mock_pm()
        ref = PackageRef.model_validate('cppython')
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        action = SetupAction(
            description="Install plugin 'cppython' to 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('mock-pm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        cmd = get_cli_command(
            action,
            _make_plugins(environments, project_environments),
            SyncStrategy.MINIMAL,
        )
        assert cmd == tuple(mock_pm.plugin_install_command(ref))

    @staticmethod
    def test_poetry_latest_delegates_upgrade_to_install() -> None:
        """Poetry: LATEST returns same as install (Poetry upgrade delegates to install)."""
        poetry_env = PoetryEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export')
        project_environments: dict[str, ProjectEnvironment] = {'poetryproject': poetry_env}

        action = SetupAction(
            description="Upgrade plugin 'poetry-plugin-export' to 'poetry'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('poetry'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        with patch.object(type(poetry_env), 'is_available', return_value=True):
            cmd = get_cli_command(
                action,
                _make_plugins(environments, project_environments),
                SyncStrategy.LATEST,
            )

        # Poetry delegates upgrade to install -- verify via protocol method
        assert cmd == tuple(poetry_env.plugin_upgrade_command(ref))
        assert cmd == tuple(poetry_env.plugin_install_command(ref))


# ---------------------------------------------------------------------------
# Extras-aware resolution (REINSTALL)
# ---------------------------------------------------------------------------


class TestExtrasReinstall:
    """Test extras-aware resolution with introspection.

    Non-Python environments (``MagicMock(spec=Environment)``) skip the
    extras introspection entirely and behave as before.
    Python environments (``MagicMock(spec=PythonEnvironment)``) trigger
    ``check_extras_installed`` -- the subprocess is mocked here.
    """

    @staticmethod
    def _make_action(
        name: str = 'cppython[cmake,conan,git]',
        plugin_target: str | None = None,
        installer: str = 'pipx',
    ) -> SetupAction:
        return setup_action(name, installer=installer, target=plugin_target)

    @staticmethod
    def _make_envs(
        installed: list[Package] | None = None,
    ) -> dict[str, Environment]:
        return {'pipx': make_environment(tool_name='pipx', installed=installed)}

    @staticmethod
    def _make_python_envs(
        installed: list[Package] | None = None,
    ) -> dict[str, Environment]:
        """Build env dict with a PythonEnvironment mock (triggers introspection)."""
        env = MagicMock(spec=PythonEnvironment)
        env.packages.return_value = installed or []
        env.check_updates.return_value = []
        env.tool_name.return_value = 'pipx'
        env.python_command.return_value = 'python'
        env.package_python.return_value = None  # default: no per-package venv
        type(env).package_name_validator = MagicMock(return_value='pep440')
        return {'pipx': env}

    # --- MINIMAL: non-Python env (no introspection) ---

    async def test_minimal_extras_non_python_env_skips(self) -> None:
        """MINIMAL + installed + extras + non-Python env -> SKIP."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED

    async def test_minimal_no_extras_installed_skips(self) -> None:
        """MINIMAL + installed + no extras -> SKIP (unchanged behaviour)."""
        action = self._make_action(name='cppython')
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED

    async def test_minimal_extras_not_installed_installs(self) -> None:
        """MINIMAL + not installed + extras -> INSTALL (normal path)."""
        action = self._make_action()
        envs = self._make_envs(installed=[])

        resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Install)

    # --- MINIMAL: Python env (introspection) ---

    async def test_minimal_extras_satisfied_skips(self) -> None:
        """MINIMAL + Python env + extras satisfied -> SKIP."""
        action = self._make_action()
        envs = self._make_python_envs(installed=[Package(name='cppython', version='1.0.0')])

        with patch(
            'porringer.backend.command.core.resolution.check_extras_installed',
            return_value=True,
        ):
            resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED

    async def test_minimal_extras_not_satisfied_reinstalls(self) -> None:
        """MINIMAL + Python env + extras not satisfied -> ENSURE_EXTRAS."""
        action = self._make_action()
        envs = self._make_python_envs(installed=[Package(name='cppython', version='1.0.0')])

        with patch(
            'porringer.backend.command.core.resolution.check_extras_installed',
            return_value=False,
        ):
            resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Install)
        assert resolved.operation.reason == InstallReason.ENSURE_EXTRAS
        assert resolved.message == 'ensuring extras'

    async def test_minimal_extras_introspection_fails_reinstalls(self) -> None:
        """MINIMAL + Python env + introspection fails -> ENSURE_EXTRAS (conservative)."""
        action = self._make_action()
        envs = self._make_python_envs(installed=[Package(name='cppython', version='1.0.0')])

        with patch(
            'porringer.backend.command.core.resolution.check_extras_installed',
            return_value=None,
        ):
            resolved = await resolve_operation(action, envs, SyncStrategy.MINIMAL)
        assert isinstance(resolved.operation, Install)
        assert resolved.operation.reason == InstallReason.ENSURE_EXTRAS

    # --- LATEST strategy ---

    async def test_latest_extras_non_python_env_skips(self) -> None:
        """LATEST + non-Python env + extras -> SKIP (extras are PEP 508 only)."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_LATEST

    async def test_latest_extras_satisfied_skips(self) -> None:
        """LATEST + Python env + extras satisfied -> SKIP(ALREADY_LATEST)."""
        action = self._make_action()
        envs = self._make_python_envs(installed=[Package(name='cppython', version='1.0.0')])

        with patch(
            'porringer.backend.command.core.resolution.check_extras_installed',
            return_value=True,
        ):
            resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_LATEST

    async def test_latest_extras_not_satisfied_reinstalls(self) -> None:
        """LATEST + Python env + extras not satisfied -> ENSURE_EXTRAS."""
        action = self._make_action()
        envs = self._make_python_envs(installed=[Package(name='cppython', version='1.0.0')])

        with patch(
            'porringer.backend.command.core.resolution.check_extras_installed',
            return_value=False,
        ):
            resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Install)
        assert resolved.operation.reason == InstallReason.ENSURE_EXTRAS

    async def test_latest_no_extras_at_latest_skips(self) -> None:
        """LATEST + installed at latest + no extras -> SKIP (unchanged)."""
        action = self._make_action(name='cppython')
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = await resolve_operation(action, envs, SyncStrategy.LATEST)
        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_LATEST

    # --- Plugin resolution ---

    async def test_plugin_minimal_no_extras_skips(self) -> None:
        """Plugin: MINIMAL + installed + no extras -> SKIP."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = self._make_action(name='cppython', plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.MINIMAL,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED
        assert resolved.plugin_manager is mock_pm

    async def test_plugin_minimal_extras_satisfied_skips(self) -> None:
        """Plugin: MINIMAL + installed + extras + introspection satisfied -> SKIP."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        mock_pm.tool_python = MagicMock(return_value='/usr/bin/python')
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        with patch(
            'porringer.backend.command.core.resolution.fetch_plugin_extras_context',
            return_value=([], frozenset()),
        ):
            resolved = await resolve_operation(
                action,
                {},
                SyncStrategy.MINIMAL,
                ResolutionContext(project_environments=proj_envs),
            )

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED
        assert resolved.plugin_manager is mock_pm

    async def test_plugin_minimal_extras_not_satisfied_reinstalls(self) -> None:
        """Plugin: MINIMAL + installed + extras not satisfied -> ENSURE_EXTRAS."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        mock_pm.tool_python = MagicMock(return_value='/usr/bin/python')
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        with patch(
            'porringer.backend.command.core.resolution.fetch_plugin_extras_context',
            return_value=(['dep-a ; extra == "cmake"'], frozenset()),
        ):
            resolved = await resolve_operation(
                action,
                {},
                SyncStrategy.MINIMAL,
                ResolutionContext(project_environments=proj_envs),
            )

        assert isinstance(resolved.operation, Install)
        assert resolved.operation.reason == InstallReason.ENSURE_EXTRAS

    async def test_plugin_minimal_extras_tool_python_not_found_reinstalls(self) -> None:
        """Plugin: MINIMAL + installed + extras + tool_python() returns None -> ENSURE_EXTRAS."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        # tool_python() returns None by default on MockPluginManager
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = await resolve_operation(
            action,
            {},
            SyncStrategy.MINIMAL,
            ResolutionContext(project_environments=proj_envs),
        )

        assert isinstance(resolved.operation, Install)
        assert resolved.operation.reason == InstallReason.ENSURE_EXTRAS

    # --- Execution routing ---

    async def test_execute_minimal_extras_skips(self) -> None:
        """execute_package under MINIMAL with extras skips when satisfied."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        mock_pm.tool_python = MagicMock(return_value='/usr/bin/python')
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = ResolutionContext(project_environments=proj_envs)

        with patch(
            'porringer.backend.command.core.resolution.fetch_plugin_extras_context',
            return_value=([], frozenset()),
        ):
            result = await execute_package(action, {}, SyncStrategy.MINIMAL, asyncio.Queue(), context)
        assert result.success is True
        assert result.skipped is True
        assert len(mock_pm.operations) == 0
