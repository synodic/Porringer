"""Helpers for test command plugin.

Test the command 'plugin'.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.schema import LocalConfiguration


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
