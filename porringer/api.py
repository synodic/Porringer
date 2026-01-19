"""API for Porringer"""

from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import SelfCommands
from porringer.backend.command.setup import SetupCommands
from porringer.backend.command.update import UpdateCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.schema import (
    APIParameters,
    LocalConfiguration,
)


class API:
    """API for programmatic access to Porringer's functionality."""

    def __init__(self, local_configuration: LocalConfiguration, parameters: APIParameters) -> None:
        """Initializes the API

        Args:
            local_configuration: The local configuration.
            parameters: The API parameters including logger.
        """
        self.configuration: Configuration = resolve_configuration(local_configuration, GlobalConfiguration())
        self.parameters = parameters
        self.plugin = PluginCommands(self.parameters.logger)
        self.porringer = SelfCommands(self.parameters.logger)
        self.setup = SetupCommands(self.parameters.logger)
        self.update = UpdateCommands(self.parameters.logger)
