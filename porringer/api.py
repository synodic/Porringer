"""API for Porringer"""

from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.command.self import SelfCommands
from porringer.backend.command.sync import SyncCommands
from porringer.backend.resolver import resolve_configuration
from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.schema import (
    LocalConfiguration,
)


class API:
    """API for programmatic access to Porringer's functionality."""

    def __init__(
        self,
        local_configuration: LocalConfiguration,
        global_configuration: GlobalConfiguration | None = None,
    ) -> None:
        """Initializes the API

        Args:
            local_configuration: The local configuration.
            global_configuration: Optional global configuration (uses defaults if not provided).
        """
        if global_configuration is None:
            global_configuration = GlobalConfiguration()

        self.configuration: Configuration = resolve_configuration(local_configuration, global_configuration)

        # Cache manager for directory storage
        self.cache = DirectoryCacheManager(self.configuration.data_directory)

        self.plugin = PluginCommands()
        self.updates = SelfCommands()
        self.sync = SyncCommands(self.cache)
