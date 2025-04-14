"""The plugin command module."""

from logging import Logger

from porringer.backend.builder import Builder
from porringer.backend.resolver import resolve_list_plugins_parameters
from porringer.schema import ListPluginResults, ListPluginsParameters


class PluginCommands:
    """Plugin commands"""

    def __init__(self, logger: Logger) -> None:
        """Initialize the SelfCommands class.

        Args:
            logger (Logger): Logger instance for logging actions.
        """
        self.logger = logger

    def list(self, parameters: ListPluginsParameters) -> list[ListPluginResults]:
        """Lists the plugins.

        Args:
            parameters: The list command parameters.

        Returns:
            A list of registered plugins.
        """
        self.logger.info('Listing plugins')

        builder = Builder(self.logger)

        environment_types = builder.find_environments()

        environments = builder.build_environments(environment_types)

        return resolve_list_plugins_parameters(environments)

    def install(self, logger: Logger) -> None:
        """Install a plugin"""
        logger.info('Installing plugin')

        builder = Builder(logger)

        environment_types = builder.find_environments()

    def uninstall(self, logger: Logger) -> None:
        """Remove an installed plugin"""
        logger.info('Uninstalling plugin')

        builder = Builder(logger)

        environment_types = builder.find_environments()

    def update(self, logger: Logger) -> None:
        """Updates the plugins.

        Args:
            logger: The logger.
        """
        logger.info('Updating plugins')
