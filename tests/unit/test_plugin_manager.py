"""Tests for the PluginManager protocol and native plugin management routing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from packaging.version import Version

from porringer.backend.command.core.action_builder import get_cli_command
from porringer.backend.command.core.execution import PluginContext, execute_package
from porringer.backend.command.core.presence import dry_run_action
from porringer.backend.command.core.resolution import OperationKind, ResolutionContext, resolve_operation
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.plugin.pdm.plugin import PdmProjectEnvironment
from porringer.plugin.poetry.plugin import PoetryProjectEnvironment
from porringer.schema import (
    SetupAction,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from porringer.test.mock.plugin_manager import MockPluginManager

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
        cmd = plugin.plugin_add_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd

    @staticmethod
    def test_pdm_plugin_add_command_with_constraint() -> None:
        """PDM plugin_add_command includes version constraint."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython>=0.5')
        cmd = plugin.plugin_add_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd

    @staticmethod
    def test_poetry_plugin_add_command_bare() -> None:
        """Poetry plugin_add_command for a bare package name."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export')
        cmd = plugin.plugin_add_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd

    @staticmethod
    def test_poetry_plugin_add_command_with_constraint() -> None:
        """Poetry plugin_add_command includes version constraint."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export>=1.0,<2.0')
        cmd = plugin.plugin_add_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd


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
    def _make_mock_pm(*, installed: list[Package] | None = None) -> MockPluginManager:
        return MockPluginManager(_MOCK_PARAMS, installed=installed)

    def test_native_command_when_plugin_manager_available(self) -> None:
        """get_cli_command returns native command when PluginManager is on PATH."""
        mock_pm = self._make_mock_pm()
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        ref = PackageRef.model_validate('cppython')

        action = SetupAction(
            description="Add 'cppython' to 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('mock-pm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        cmd = get_cli_command(action, environments, SyncStrategy.MINIMAL, project_environments)
        assert cmd == mock_pm.plugin_add_command(ref)

    def test_empty_command_when_plugin_manager_unavailable(self) -> None:
        """get_cli_command returns empty list when PluginManager tool is not on PATH."""
        pdm_env = PdmProjectEnvironment(_MOCK_PARAMS)
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
        mock_pm = MockPluginManager(_MOCK_PARAMS)

        action = SetupAction(
            description="Add 'cppython' to 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=PackageRef.model_validate('cppython'),
            plugin_target=PackageRef.model_validate('mock-pm'),
        )

        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        async def _run():
            return await execute_package(action, {}, SyncStrategy.MINIMAL, None, context)

        result = asyncio.run(_run())
        assert result.success is True
        assert result.message is not None
        assert 'native' in result.message.lower()
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'add'

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


# ---------------------------------------------------------------------------
# Plugin list / query
# ---------------------------------------------------------------------------


class TestPluginListCommand:
    """Test the plugin_list_command implementations."""

    @staticmethod
    def test_pdm_plugin_list_command() -> None:
        """PDM plugin_list_command starts with tool name."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        cmd = plugin.plugin_list_command()
        assert cmd[0] == plugin.tool_name()
        assert len(cmd) > 1

    @staticmethod
    def test_poetry_plugin_list_command() -> None:
        """Poetry plugin_list_command starts with tool name."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        cmd = plugin.plugin_list_command()
        assert cmd[0] == plugin.tool_name()
        assert len(cmd) > 1


class TestParsePluginList:
    """Test plugin list output parsing."""

    @staticmethod
    def test_pdm_parse_with_version_and_description() -> None:
        """PDM parser extracts name and version from tabular output."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        stdout = 'cppython  0.9.14  A Python management solution for C++\n'
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 1
        assert result[0].name == 'cppython'
        assert result[0].version == '0.9.14'

    @staticmethod
    def test_pdm_parse_multiple_plugins() -> None:
        """PDM parser handles multiple lines."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        stdout = 'cppython 0.9.14 desc\npoetry-plugin 1.0.0 other\n'
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 2
        assert result[0].name == 'cppython'
        assert result[1].name == 'poetry-plugin'

    @staticmethod
    def test_pdm_parse_empty_output() -> None:
        """PDM parser returns empty list for empty output."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        assert plugin.parse_plugin_list('') == []
        assert plugin.parse_plugin_list('\n') == []

    @staticmethod
    def test_poetry_parse_plugin_line() -> None:
        """Poetry parser extracts name and version from indented lines."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        stdout = '  - poetry-plugin-export (1.6.0) Poetry plugin\n'
        result = plugin.parse_plugin_list(stdout)
        assert len(result) == 1
        assert result[0].name == 'poetry-plugin-export'
        assert result[0].version == '1.6.0'

    @staticmethod
    def test_poetry_parse_ignores_non_plugin_lines() -> None:
        """Poetry parser skips header/dependency lines."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
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
    def test_installed_plugins_success() -> None:
        """installed_plugins parses subprocess output on success."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'cppython 0.9.14 desc\n'
        mock_result.stderr = ''

        with patch('porringer.core.plugin_schema.plugin_manager.subprocess.run', return_value=mock_result):
            result = plugin.installed_plugins()

        assert len(result) == 1
        assert result[0].name == 'cppython'

    @staticmethod
    def test_installed_plugins_failure_returns_empty() -> None:
        """installed_plugins returns empty list on non-zero exit."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'error'

        with patch('porringer.core.plugin_schema.plugin_manager.subprocess.run', return_value=mock_result):
            result = plugin.installed_plugins()

        assert result == []

    @staticmethod
    def test_installed_plugins_file_not_found() -> None:
        """installed_plugins returns empty list when tool is missing."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)

        with patch(
            'porringer.core.plugin_schema.plugin_manager.subprocess.run',
            side_effect=FileNotFoundError,
        ):
            result = plugin.installed_plugins()

        assert result == []


# ---------------------------------------------------------------------------
# Plugin presence in dry-run
# ---------------------------------------------------------------------------

_PLUGIN_ACTION = SetupAction(
    description="Add 'cppython' to 'mock-pm'",
    kind=PluginKind.TOOL,
    ecosystem=_PY,
    installer='pipx',
    package=PackageRef.model_validate('cppython'),
    plugin_target=PackageRef.model_validate('mock-pm'),
)


class TestDryRunPluginPresence:
    """Test that dry_run_action correctly queries PluginManager for plugin-target actions."""

    @staticmethod
    def _make_envs(
        *,
        installed: list[Package] | None = None,
    ) -> tuple[MockPluginManager, dict[str, Environment], dict[str, ProjectEnvironment]]:
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=installed)
        mock_pipx = MagicMock(spec=Environment)
        mock_pipx.packages.return_value = []
        environments: dict[str, Environment] = {'pipx': mock_pipx}
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        return mock_pm, environments, project_environments

    def test_skips_when_plugin_installed(self) -> None:
        """dry_run_action skips plugin-target action when plugin is already installed."""
        _, environments, project_environments = self._make_envs(
            installed=[Package(name='cppython', version='0.9.14')],
        )

        result = dry_run_action(
            _PLUGIN_ACTION,
            environments,
            project_environments=project_environments,
        )

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    def test_not_skipped_when_plugin_missing(self) -> None:
        """dry_run_action does not skip when plugin is not installed."""
        _, environments, project_environments = self._make_envs(installed=[])

        result = dry_run_action(
            _PLUGIN_ACTION,
            environments,
            project_environments=project_environments,
        )

        assert result.skipped is not True

    def test_not_skipped_when_no_plugin_manager(self) -> None:
        """dry_run_action does not skip when no PluginManager is available."""
        _, environments, _ = self._make_envs()

        result = dry_run_action(
            _PLUGIN_ACTION,
            environments,
            project_environments=None,
        )

        assert result.skipped is not True

    def test_upgrade_when_plugin_installed_latest_strategy(self) -> None:
        """dry_run_action reports upgrade intent when LATEST strategy and plugin is installed."""
        _, environments, project_environments = self._make_envs(
            installed=[Package(name='cppython', version='0.9.14')],
        )
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(
            _PLUGIN_ACTION,
            environments,
            project_environments=project_environments,
            parameters=params,
        )

        # LATEST + installed → upgrade, not skipped
        assert result.skipped is not True
        assert result.success is True

    def test_install_when_plugin_missing_latest_strategy(self) -> None:
        """dry_run_action reports install when LATEST strategy and plugin is missing."""
        _, environments, project_environments = self._make_envs(installed=[])
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(
            _PLUGIN_ACTION,
            environments,
            project_environments=project_environments,
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
    def test_skips_installed_plugin_on_minimal() -> None:
        """execute_package skips plugin-target action when already installed."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        async def _run():
            return await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, None, context)

        result = asyncio.run(_run())
        assert result.success is True
        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert len(mock_pm.operations) == 0

    @staticmethod
    def test_installs_missing_plugin_on_minimal() -> None:
        """execute_package installs plugin when not already installed."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        async def _run():
            return await execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, None, context)

        result = asyncio.run(_run())
        assert result.success is True
        assert result.skipped is not True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'add'


# ---------------------------------------------------------------------------
# plugin_update_command
# ---------------------------------------------------------------------------


class TestPluginUpdateCommand:
    """Test the plugin_update_command implementations."""

    @staticmethod
    def test_pdm_plugin_update_command_bare() -> None:
        """PDM plugin_update_command starts with tool name and includes the package."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        cmd = plugin.plugin_update_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd
        # Update must differ from add (add omits the upgrade mechanism)
        assert cmd != plugin.plugin_add_command(ref)

    @staticmethod
    def test_pdm_plugin_update_command_with_constraint() -> None:
        """PDM plugin_update_command includes version constraint."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython>=0.5')
        cmd = plugin.plugin_update_command(ref)
        assert cmd[0] == plugin.tool_name()
        assert ref.specifier in cmd
        assert cmd != plugin.plugin_add_command(ref)

    @staticmethod
    def test_poetry_plugin_update_delegates_to_add() -> None:
        """Poetry plugin_update_command delegates to plugin_add_command."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export')
        update_cmd = plugin.plugin_update_command(ref)
        add_cmd = plugin.plugin_add_command(ref)
        assert update_cmd == add_cmd

    @staticmethod
    def test_poetry_plugin_update_with_constraint() -> None:
        """Poetry plugin_update_command with constraint delegates to add."""
        plugin = PoetryProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('poetry-plugin-export>=1.0')
        update_cmd = plugin.plugin_update_command(ref)
        add_cmd = plugin.plugin_add_command(ref)
        assert update_cmd == add_cmd

    @staticmethod
    def test_pdm_update_includes_pip_upgrade_flag() -> None:
        """PDM update must pass --pip-args --upgrade so pip actually upgrades.

        Without --pip-args --upgrade, ``pdm self add <pkg>`` delegates to
        ``pip install <pkg>`` which is a no-op when the package is already
        installed — pip sees the requirement satisfied and skips the upgrade.
        This test reproduces the bug where ``pdm self add cppython`` reported
        success but left the old version in place.
        """
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')

        add_cmd = plugin.plugin_add_command(ref)
        update_cmd = plugin.plugin_update_command(ref)

        # add intentionally omits the upgrade flag (first install)
        assert '--pip-args' not in add_cmd
        assert '--upgrade' not in add_cmd

        # update MUST include the flag so pip pulls a newer version
        assert '--pip-args' in update_cmd
        assert '--upgrade' in update_cmd

        # The two commands must not be identical
        assert add_cmd != update_cmd


# ---------------------------------------------------------------------------
# async_plugin_update
# ---------------------------------------------------------------------------


class TestAsyncPluginUpdate:
    """Test the async_plugin_update default implementation."""

    @staticmethod
    def test_async_plugin_update_success() -> None:
        """async_plugin_update returns Package on success."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Updated cppython'
        mock_result.stderr = ''

        async def _run() -> Package | None:
            with patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd:
                mock_cmd.return_value = mock_result
                return await plugin.async_plugin_update(params)

        result = asyncio.run(_run())
        assert result is not None
        assert result.name == 'cppython'

    @staticmethod
    def test_async_plugin_update_failure() -> None:
        """async_plugin_update returns None on non-zero exit."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Error: no such plugin'

        async def _run() -> Package | None:
            with patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd:
                mock_cmd.return_value = mock_result
                return await plugin.async_plugin_update(params)

        result = asyncio.run(_run())
        assert result is None

    @staticmethod
    def test_async_plugin_update_file_not_found() -> None:
        """async_plugin_update returns None when tool is not on PATH."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        async def _run() -> Package | None:
            with patch(
                'porringer.core.plugin_schema.plugin_manager.run_command',
                new_callable=AsyncMock,
                side_effect=FileNotFoundError,
            ):
                return await plugin.async_plugin_update(params)

        result = asyncio.run(_run())
        assert result is None

    @staticmethod
    def test_async_plugin_update_uses_update_command() -> None:
        """async_plugin_update delegates to plugin_update_command."""
        plugin = PdmProjectEnvironment(_MOCK_PARAMS)
        ref = PackageRef.model_validate('cppython')
        params = PackageParameters(package=ref)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Updated cppython'
        mock_result.stderr = ''

        async def _run() -> list[str]:
            with patch('porringer.core.plugin_schema.plugin_manager.run_command', new_callable=AsyncMock) as mock_cmd:
                mock_cmd.return_value = mock_result
                await plugin.async_plugin_update(params)
                return mock_cmd.call_args[0][0]

        called_args = asyncio.run(_run())
        # The default implementation delegates to plugin_update_command
        assert called_args == plugin.plugin_update_command(ref)


# ---------------------------------------------------------------------------
# resolve_operation — unified resolution tests
# ---------------------------------------------------------------------------


class TestResolveOperation:
    """Test resolve_operation with different strategies and states."""

    @staticmethod
    def _make_action(
        plugin_target: str | None = None,
        name: str = 'cppython',
        installer: str = 'pipx',
    ) -> SetupAction:
        pkg = PackageRef.model_validate(name)
        target = PackageRef.model_validate(plugin_target) if plugin_target else None
        return SetupAction(
            description=f"Install '{name}'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=installer,
            package=pkg,
            plugin_target=target,
        )

    @staticmethod
    def _make_envs(
        installed: list[Package] | None = None,
    ) -> dict[str, Environment]:
        env = MagicMock(spec=Environment)
        env.packages.return_value = installed or []
        type(env).package_name_validator = MagicMock(return_value='pep440')
        return {'pipx': env}

    # --- Normal package resolution ---

    def test_minimal_installed_skips(self) -> None:
        """MINIMAL + installed → SKIP."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.MINIMAL))
        assert resolved.operation == OperationKind.SKIP
        assert resolved.skip_reason == SkipReason.ALREADY_INSTALLED

    def test_minimal_not_installed_installs(self) -> None:
        """MINIMAL + not installed → INSTALL."""
        action = self._make_action()
        envs = self._make_envs(installed=[])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.MINIMAL))
        assert resolved.operation == OperationKind.INSTALL

    def test_latest_installed_upgrades(self) -> None:
        """LATEST + installed → UPGRADE."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.LATEST))
        assert resolved.operation == OperationKind.UPGRADE

    def test_latest_not_installed_installs(self) -> None:
        """LATEST + not installed → INSTALL (fallback)."""
        action = self._make_action()
        envs = self._make_envs(installed=[])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.LATEST))
        assert resolved.operation == OperationKind.INSTALL

    def test_exact_installed_upgrades(self) -> None:
        """EXACT + installed → UPGRADE."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='1.0.0')])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.EXACT))
        assert resolved.operation == OperationKind.UPGRADE

    # --- Plugin-management resolution ---

    def test_plugin_minimal_installed_skips(self) -> None:
        """Plugin: MINIMAL + installed → SKIP."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = asyncio.run(
            resolve_operation(action, {}, SyncStrategy.MINIMAL, ResolutionContext(project_environments=proj_envs))
        )

        assert resolved.operation == OperationKind.SKIP
        assert resolved.skip_reason == SkipReason.ALREADY_INSTALLED
        assert resolved.plugin_manager is mock_pm

    def test_plugin_minimal_not_installed_installs(self) -> None:
        """Plugin: MINIMAL + not installed → INSTALL."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = asyncio.run(
            resolve_operation(action, {}, SyncStrategy.MINIMAL, ResolutionContext(project_environments=proj_envs))
        )

        assert resolved.operation == OperationKind.INSTALL
        assert resolved.plugin_manager is mock_pm

    def test_plugin_latest_installed_upgrades(self) -> None:
        """Plugin: LATEST + installed → UPGRADE."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = asyncio.run(
            resolve_operation(action, {}, SyncStrategy.LATEST, ResolutionContext(project_environments=proj_envs))
        )

        assert resolved.operation == OperationKind.UPGRADE
        assert resolved.plugin_manager is mock_pm

    def test_plugin_latest_not_installed_installs(self) -> None:
        """Plugin: LATEST + not installed → INSTALL (fallback)."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        action = self._make_action(plugin_target='mock-pm')
        proj_envs: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        resolved = asyncio.run(
            resolve_operation(action, {}, SyncStrategy.LATEST, ResolutionContext(project_environments=proj_envs))
        )

        assert resolved.operation == OperationKind.INSTALL

    def test_plugin_no_manager_defaults_to_install(self) -> None:
        """Plugin: no PluginManager → INSTALL."""
        action = self._make_action(plugin_target='mock-pm')

        resolved = asyncio.run(resolve_operation(action, {}, SyncStrategy.MINIMAL))

        assert resolved.operation == OperationKind.INSTALL
        assert resolved.plugin_manager is None

    def test_skip_returns_installed_version(self) -> None:
        """SKIP result carries installed_version metadata."""
        action = self._make_action()
        envs = self._make_envs(installed=[Package(name='cppython', version='2.3.1')])

        resolved = asyncio.run(resolve_operation(action, envs, SyncStrategy.MINIMAL))
        assert resolved.operation == OperationKind.SKIP
        assert resolved.installed_version == '2.3.1'

    def test_missing_installer_skips(self) -> None:
        """Action with no installer/package → SKIP."""
        action = SetupAction(
            description='Bad action',
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=None,
            package=None,
        )
        resolved = asyncio.run(resolve_operation(action, {}, SyncStrategy.MINIMAL))
        assert resolved.operation == OperationKind.SKIP


# ---------------------------------------------------------------------------
# Plugin upgrade routing in execute_package
# ---------------------------------------------------------------------------


class TestPluginUpgradeRouting:
    """Test that execute_package routes to async_plugin_update for LATEST strategy."""

    @staticmethod
    def test_latest_routes_to_update() -> None:
        """execute_package with LATEST calls async_plugin_update for installed plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[Package(name='cppython', version='0.9.14')])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        result = asyncio.run(execute_package(_PLUGIN_ACTION, {}, SyncStrategy.LATEST, None, context))
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'update'
        assert mock_pm.operations[0][1].name == 'cppython'

    @staticmethod
    def test_latest_installs_when_not_present() -> None:
        """execute_package with LATEST calls async_plugin_add for missing plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        result = asyncio.run(execute_package(_PLUGIN_ACTION, {}, SyncStrategy.LATEST, None, context))
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'add'

    @staticmethod
    def test_minimal_always_uses_add() -> None:
        """execute_package with MINIMAL uses async_plugin_add for new plugin."""
        mock_pm = MockPluginManager(_MOCK_PARAMS, installed=[])
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}
        context = PluginContext(project_environments=project_environments)

        result = asyncio.run(execute_package(_PLUGIN_ACTION, {}, SyncStrategy.MINIMAL, None, context))
        assert result.success is True
        assert len(mock_pm.operations) == 1
        assert mock_pm.operations[0][0] == 'add'


# ---------------------------------------------------------------------------
# CLI command preview with upgrade strategies
# ---------------------------------------------------------------------------


class TestCliCommandUpgradePreview:
    """Test that get_cli_command returns upgrade commands for LATEST/EXACT."""

    @staticmethod
    def _make_mock_pm() -> MockPluginManager:
        return MockPluginManager(_MOCK_PARAMS)

    def test_latest_returns_update_command(self) -> None:
        """get_cli_command returns plugin_update_command for LATEST strategy."""
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

        cmd = get_cli_command(action, environments, SyncStrategy.LATEST, project_environments)
        assert cmd == mock_pm.plugin_update_command(ref)

    def test_exact_returns_update_command(self) -> None:
        """get_cli_command returns plugin_update_command for EXACT strategy."""
        mock_pm = self._make_mock_pm()
        ref = PackageRef.model_validate('cppython')
        project_environments: dict[str, ProjectEnvironment] = {'mockpmproject': mock_pm}

        action = SetupAction(
            description="Ensure plugin 'cppython' to 'mock-pm'",
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer='pipx',
            package=ref,
            plugin_target=PackageRef.model_validate('mock-pm'),
        )

        mock_env = MagicMock(spec=Environment)
        environments: dict[str, Environment] = {'pipx': mock_env}

        cmd = get_cli_command(action, environments, SyncStrategy.EXACT, project_environments)
        assert cmd == mock_pm.plugin_update_command(ref)

    def test_minimal_returns_add_command(self) -> None:
        """get_cli_command returns plugin_add_command for MINIMAL strategy."""
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

        cmd = get_cli_command(action, environments, SyncStrategy.MINIMAL, project_environments)
        assert cmd == mock_pm.plugin_add_command(ref)

    def test_poetry_latest_delegates_update_to_add(self) -> None:
        """Poetry: LATEST returns same as add (Poetry update delegates to add)."""
        poetry_env = PoetryProjectEnvironment(_MOCK_PARAMS)
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
            cmd = get_cli_command(action, environments, SyncStrategy.LATEST, project_environments)

        # Poetry delegates update to add — verify via protocol method
        assert cmd == poetry_env.plugin_update_command(ref)
        assert cmd == poetry_env.plugin_add_command(ref)
