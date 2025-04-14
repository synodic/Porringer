"""Test the command 'plugin'"""

from logging import Logger

from porringer.api import API
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
