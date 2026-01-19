"""Test the command 'plugin'"""

from logging import Logger
from unittest.mock import MagicMock, patch

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.schema import APIParameters, ListPluginsParameters, LocalConfiguration


class TestCommandPlugin:
    """Test the command 'plugin'"""

    @staticmethod
    def test_plugin_list() -> None:
        """Test the plugin list"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.plugin.list(params)

        assert results

    @staticmethod
    def test_plugin_update() -> None:
        """Test the plugin update"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.plugin.list(params)

        assert results

    @staticmethod
    def test_plugin_list_with_missing_module() -> None:
        """Test that plugin listing handles ModuleNotFoundError gracefully.

        This reproduces an issue where a downstream project, a frozen application,
        has registered entry points for plugins that cannot be imported because the
        module doesn't exist in that context.
        """
        logger = Logger('test')
        builder = Builder(logger)

        # Create a mock entry point that raises ModuleNotFoundError when loaded
        mock_entry_point = MagicMock()
        mock_entry_point.load.side_effect = ModuleNotFoundError("No module named 'porringer.plugin")
        mock_entry_point.name = 'missing_plugin'

        with patch('porringer.backend.builder.metadata.entry_points') as mock_entry_points:
            mock_entry_points.return_value = [mock_entry_point]

            # This should not raise an exception - it should handle the error gracefully
            result = builder.find_environments()

            # The result should be empty since the plugin couldn't be loaded
            assert result == []
