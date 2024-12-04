"""API for Porringer"""

from porringer.backend.command.plugin import list_plugins, update_plugins
from porringer.backend.command.self import check_porringer, update_porringer
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
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
        """Initializes the API"""
        self.configuration: Configuration = resolve_configuration(local_configuration, GlobalConfiguration())
        self.parameters = parameters

    def update_porringer(self, _: UpdatePorringerParameters) -> None:
        """Updates Porringer

        Raises:
            NotImplementedError:
        """
        update_porringer(self.parameters.logger)

    def check_porringer(self, _: CheckPorringerParameters) -> None:
        """Checks for an update

        Raises:
            NotImplementedError:
        """
        check_porringer(self.parameters.logger)

    def list_plugins(self, parameters: ListPluginsParameters) -> list[ListPluginResults]:
        """Gather and return a list of plugins

        Args:
            parameters: The list command parameters.

        Returns:
            The list of plugins
        """
        return list_plugins(parameters, self.parameters.logger)

    def update_plugins(self, _: UpdatePluginsParameters) -> None:
        """Updates all plugins that are not managed by the application"""
        update_plugins(self.parameters.logger)
