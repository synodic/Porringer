"""API for Porringer"""

from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import SelfCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.schema import (
    APIParameters,
    LocalConfiguration,
)


class API:
    """_summary_"""

    def __init__(self, local_configuration: LocalConfiguration, parameters: APIParameters) -> None:
        """Initializes the API"""
        self.configuration: Configuration = resolve_configuration(local_configuration, GlobalConfiguration())
        self.parameters = parameters
        self.plugin = PluginCommands(self.parameters.logger)
        self.porringer = SelfCommands(self.parameters.logger)
