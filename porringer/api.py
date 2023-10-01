"""API for Porringer"""

from porringer.backend.plugin import list_plugins, update_plugins
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.backend.self import check_porringer, update_porringer
from porringer.schema import (
    CheckPorringerParameters,
    ListPluginResults,
    ListPluginsParameters,
    LocalConfiguration,
    Parameters,
    UpdatePluginsParameters,
    UpdatePorringerParameters,
)


class API:
    """_summary_"""

    def __init__(self, local_configuration: LocalConfiguration, parameters: Parameters) -> None:
        self.configuration: Configuration = resolve_configuration(local_configuration, GlobalConfiguration())
        self.parameters = parameters

    def update_porringer(self, _: UpdatePorringerParameters) -> None:
        """_summary_

        Raises:
            NotImplementedError: _description_
        """
        update_porringer(self.parameters.logger)

    def check_porringer(self, _: CheckPorringerParameters) -> None:
        """_summary_

        Raises:
            NotImplementedError: _description_
        """
        check_porringer(self.parameters.logger)

    def list_plugins(self, _: ListPluginsParameters) -> list[ListPluginResults]:
        """_summary_

        Returns:
            _description_
        """

        return list_plugins(self.parameters.logger)

    def update_plugins(self, _: UpdatePluginsParameters) -> None:
        """Updates all plugins that are not managed by the application"""

        update_plugins(self.parameters.logger)
