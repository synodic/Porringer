"""Test the command 'plugin'"""

from logging import Logger

from porringer.api import API
from porringer.schema import ListPluginsParameters, LocalConfiguration, Parameters


class TestCommandPlugin:
    """Test the command 'plugin'"""

    @staticmethod
    def test_plugin_list() -> None:
        """Test the plugin list"""
        config = LocalConfiguration()
        parameters = Parameters(logger=Logger('test'))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.list_plugins(params)

        assert results

    @staticmethod
    def test_plugin_update() -> None:
        """Test the plugin update"""
        config = LocalConfiguration()
        parameters = Parameters(logger=Logger('test'))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.list_plugins(params)

        assert results
