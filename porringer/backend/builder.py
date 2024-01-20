"""Builder"""

from importlib import metadata
from inspect import getmodule
from logging import Logger

from porringer_core.plugin_schema.environment import Environment
from synodic_utilities.exception import PluginError
from synodic_utilities.utility import canonicalize_type

from porringer.schema import PluginInformation


class Builder:
    """Helper class for building Porringer projects"""

    def __init__(self, logger: Logger) -> None:
        self.logger = logger

    def find_environments(self) -> list[PluginInformation[Environment]]:
        """Searches for registered environment plugins

        Raises:
            PluginError: Raised if there is no plugin found

        Returns:
            A list of loaded plugins
        """

        group_name = "environment"
        plugin_types: list[PluginInformation[Environment]] = []

        # Filter entries by type
        for entry_point in list(metadata.entry_points(group=f"porringer.{group_name}")):
            loaded_type = entry_point.load()

            canonicalized = canonicalize_type(loaded_type)

            if entry_point.dist is None:
                self.logger.error(f"Plugin '{canonicalized.name}' is not installed. Skipping")
                continue

            # TODO: Add metadata to plugin information, percolate to pytest_synodic API

            if not issubclass(loaded_type, Environment):
                self.logger.warning(
                    f"Found incompatible plugin. The '{canonicalized.name}' plugin must be an instance"
                    f" of '{group_name}'"
                )
            else:
                self.logger.warning(f"{group_name} plugin found: {canonicalized.name} from {getmodule(loaded_type)}")
                plugin_types.append(PluginInformation(loaded_type, entry_point.dist))

        if not plugin_types:
            raise PluginError(f"No {group_name} plugin was found")

        return plugin_types

    def build_environments(self, environment_types: list[PluginInformation[Environment]]) -> list[Environment]:
        """Constructs environments from input types

        Args:
            environment_types: The types to construct

        Returns:
            The instantiated environments
        """

        environments: list[Environment] = []

        for environment_type in environment_types:
            environments.append(environment_type.type())

        return environments
