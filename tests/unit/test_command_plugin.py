"""Helpers for test command plugin."""

"""Test the command 'plugin'."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.plugin import PluginCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.schema import LocalConfiguration
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation
from tests.fixtures.factories import command_fail, command_ok

# Test constants
NUM_PLUGINS_MULTIPLE = 3
NUM_PLUGINS_PARTIAL = 2
NUM_RESOLVED_TAGS = 2
NUM_CONCURRENT_RUNTIMES = 3


class TestAPIDiscovery:
    """Tests for API plugin discovery/runtime helpers."""

    @staticmethod
    async def test_discover_plugins_without_runtime_skips_runtime_resolution() -> None:
        """Fast discovery returns plugins without probing runtime executables."""
        discovered = DiscoveredPlugins(environments={}, project_environments={}, scm_environments={})

        with (
            patch('porringer.api.discover_all_plugins', return_value=discovered) as mock_discover,
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
        ):
            result = await API.discover_plugins(use_cache=False, resolve_runtime=False)

        mock_discover.assert_called_once_with(use_cache=False)
        mock_resolve.assert_not_called()
        assert result is discovered
        assert result.runtime_context is None

    @staticmethod
    async def test_resolve_runtime_context_attaches_context() -> None:
        """Runtime resolution can be deferred and attached to discovered plugins later."""
        discovered = DiscoveredPlugins(environments={}, project_environments={}, scm_environments={})
        context = RuntimeContext(executables={'python': Path('/python/default/python')})

        with patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=context):
            result = await API.resolve_runtime_context(discovered)

        assert result is context
        assert discovered.runtime_context is context


class TestCommandPlugin:
    """Test the command 'plugin'."""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _skip_tool_version():
        """Bypass ``tool_version()`` subprocess calls — this test only verifies listing."""
        with (
            patch('porringer.backend.resolver.ToolBasedPlugin.tool_version', return_value=None),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            yield

    @staticmethod
    async def test_plugin_list() -> None:
        """Test the plugin list."""
        config = LocalConfiguration()
        api = API(config)

        results = await api.extension.list()

        assert results
        # Each result should have an installed status based on is_available()
        for result in results:
            assert isinstance(result.installed, bool)
            # tool_version is None because we patched it above
            assert result.tool_version is None

    @staticmethod
    def test_plugin_list_with_missing_module() -> None:
        """Test that plugin listing handles ModuleNotFoundError gracefully.

        This reproduces an issue where a downstream project, a frozen application,
        has registered entry points for plugins that cannot be imported because the
        module doesn't exist in that context.
        """
        builder = Builder()

        # Create a mock entry point that raises ModuleNotFoundError when loaded
        mock_entry_point = MagicMock()
        mock_entry_point.load.side_effect = ModuleNotFoundError("No module named 'porringer.plugin")
        mock_entry_point.name = 'missing_plugin'

        with patch('porringer.backend.builder.metadata.entry_points') as mock_entry_points:
            mock_entry_points.return_value = [mock_entry_point]

            # This should not raise an exception - it should handle the error gracefully
            infos, errors = builder.find_plugins('environment', Environment)

            # The result should be empty since the plugin couldn't be loaded
            assert infos == []
            assert 'missing_plugin' in errors


class TestPluginInstall:
    """Test plugin install command."""

    @staticmethod
    async def test_install_dry_run(installer_is_pipx: bool) -> None:
        """Install dry-run emits the mode-appropriate command preview."""
        commands = PluginCommands()
        expected = 'pipx inject' if installer_is_pipx else 'pip install'

        result = await commands.install('some-plugin', dry_run=True)

        assert result.success
        assert 'Would install' in result.message
        assert expected in result.message
        assert 'some-plugin' in result.message

    @staticmethod
    async def test_install_failure_returns_error() -> None:
        """Test that install failure returns error result."""
        commands = PluginCommands()

        mock_result = command_fail('Package not found')

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', new_callable=AsyncMock, return_value=mock_result),
        ):
            result = await commands.install('nonexistent-plugin')

            assert not result.success
            assert 'failed' in result.message.lower()

    @staticmethod
    async def test_install_validates_plugin_entry_point() -> None:
        """Test that install validates the package provides entry points."""
        commands = PluginCommands()

        mock_result = command_ok('Successfully installed')

        # Mock that the package doesn't add any new entry points
        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', new_callable=AsyncMock, return_value=mock_result),
            patch.object(commands, '_get_existing_plugin_packages', return_value=set()),
        ):
            with pytest.raises(PluginError) as exc_info:
                await commands.install('not-a-plugin')

            assert 'not a valid Porringer plugin' in str(exc_info.value)

    @staticmethod
    async def test_install_command_not_found() -> None:
        """Test handling of FileNotFoundError."""
        commands = PluginCommands()

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=True),
            patch(
                'porringer.backend.command.plugin.run_command',
                new_callable=AsyncMock,
                side_effect=FileNotFoundError('pipx not found'),
            ),
        ):
            result = await commands.install('some-plugin')

            assert not result.success
            assert 'not found' in result.message.lower()


class TestPluginUninstall:
    """Test plugin uninstall command."""

    @staticmethod
    async def test_uninstall_dry_run(installer_is_pipx: bool) -> None:
        """Uninstall dry-run emits the mode-appropriate command preview."""
        commands = PluginCommands()
        expected = 'pipx uninject' if installer_is_pipx else 'pip uninstall'

        results = await commands.uninstall(['some-plugin'], dry_run=True)

        assert len(results) == 1
        assert results[0].success
        assert 'Would uninstall' in results[0].message
        assert expected in results[0].message

    @staticmethod
    async def test_uninstall_multiple_plugins() -> None:
        """Test uninstalling multiple plugins."""
        commands = PluginCommands()

        mock_result = command_ok('Successfully uninstalled')

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', new_callable=AsyncMock, return_value=mock_result),
        ):
            results = await commands.uninstall(['plugin-a', 'plugin-b', 'plugin-c'])

            assert len(results) == NUM_PLUGINS_MULTIPLE
            assert all(r.success for r in results)

    @staticmethod
    async def test_uninstall_partial_failure() -> None:
        """Test that partial failures are reported correctly."""
        commands = PluginCommands()

        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call succeeds, second fails
            if call_count == 1:
                return command_ok('Success')
            return command_fail('Package not found')

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', side_effect=mock_run),
        ):
            results = await commands.uninstall(['plugin-ok', 'plugin-fail'])

            assert len(results) == NUM_PLUGINS_PARTIAL
            assert results[0].success
            assert not results[1].success


class TestPluginUpgrade:
    """Test plugin upgrade command."""

    @staticmethod
    async def test_upgrade_dry_run(installer_is_pipx: bool) -> None:
        """Upgrade dry-run emits the mode-appropriate command preview."""
        commands = PluginCommands()
        expected = 'pipx runpip' if installer_is_pipx else '--upgrade'

        results = await commands.upgrade(['some-plugin'], dry_run=True)

        assert len(results) == 1
        assert results[0].success
        assert 'Would upgrade' in results[0].message
        assert expected in results[0].message

    @staticmethod
    async def test_upgrade_success() -> None:
        """Test successful upgrade."""
        commands = PluginCommands()

        mock_result = command_ok('Successfully upgraded')

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', new_callable=AsyncMock, return_value=mock_result),
        ):
            results = await commands.upgrade(['some-plugin'])

            assert len(results) == 1
            assert results[0].success
            assert 'Successfully upgraded' in results[0].message

    @staticmethod
    async def test_upgrade_failure() -> None:
        """Test upgrade failure."""
        commands = PluginCommands()

        mock_result = command_fail('No matching distribution')

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.run_command', new_callable=AsyncMock, return_value=mock_result),
        ):
            results = await commands.upgrade(['nonexistent-plugin'])

            assert len(results) == 1
            assert not results[0].success
            assert 'failed' in results[0].message.lower()


class TestPipxDetection:
    """Test pipx installation detection utility."""

    @staticmethod
    def test_is_pipx_installation_true() -> None:
        """Test detection when running in pipx environment."""
        # Mock sys.prefix to look like a pipx venv (using os.sep for cross-platform)
        pipx_path = os.sep.join(['', 'home', 'user', '.local', 'pipx', 'venvs', 'porringer'])
        with patch.object(sys, 'prefix', pipx_path):
            assert is_pipx_installation()

    @staticmethod
    def test_is_pipx_installation_false() -> None:
        """Test detection when running in regular venv."""
        # Mock sys.prefix to look like a regular venv
        venv_path = os.sep.join(['', 'home', 'user', 'projects', 'porringer', '.venv'])
        with patch.object(sys, 'prefix', venv_path):
            assert not is_pipx_installation()
