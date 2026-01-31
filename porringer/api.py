"""API for Porringer"""

from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import SelfCommands
from porringer.backend.command.update import UpdateCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.schema import (
    APIParameters,
    LocalConfiguration,
)


class API:
    """API for programmatic access to Porringer's functionality."""

    def __init__(
        self,
        local_configuration: LocalConfiguration,
        parameters: APIParameters,
        global_configuration: GlobalConfiguration | None = None,
    ) -> None:
        """Initializes the API

        Args:
            local_configuration: The local configuration.
            parameters: The API parameters including logger.
            global_configuration: Optional global configuration (uses defaults if not provided).
        """
        if global_configuration is None:
            global_configuration = GlobalConfiguration()

        self.configuration: Configuration = resolve_configuration(local_configuration, global_configuration)
        self.parameters = parameters

        # Cache manager for directory storage
        self.cache = DirectoryCacheManager(self.configuration.data_directory, self.parameters.logger)

        self.plugin = PluginCommands(self.parameters.logger)
        self.porringer = SelfCommands(self.parameters.logger)
        self.update = UpdateCommands(self.parameters.logger, self.cache)
