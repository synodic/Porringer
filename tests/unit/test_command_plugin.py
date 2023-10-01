"""Test the command 'plugin' """
from logging import Logger

from porringer.api import API
from porringer.schema import ListPluginsParameters, LocalConfiguration, Parameters


class TestCommandPlugin:
    """_summary_"""

    def test_plugin_list(self) -> None:
        """_summary_"""

        config = LocalConfiguration()
        parameters = Parameters(logger=Logger("test"))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.list_plugins(params)

        assert not results

    def test_plugin_update(self) -> None:
        """_summary_"""

        config = LocalConfiguration()
        parameters = Parameters(logger=Logger("test"))
        api = API(config, parameters)

        params = ListPluginsParameters()
        results = api.list_plugins(params)

        assert not results
