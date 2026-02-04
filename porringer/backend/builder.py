"""Builder"""

from importlib import metadata
from logging import Logger

from packaging.version import Version

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Distribution, PluginDependency, PluginParameters
from porringer.schema import PluginInformation
from porringer.utility.exception import PluginDependencyError
from porringer.utility.utility import canonicalize_type


class Builder:
    """Helper class for building Porringer projects"""

    def __init__(self, logger: Logger) -> None:
        """Initializes the builder"""
        self.logger = logger

    def find_environments(self, check_dependencies: bool = True) -> list[PluginInformation[Environment]]:
        """Searches for registered environment plugins

        Args:
            check_dependencies: If True, validates plugin dependencies and filters
                               out plugins with unmet required dependencies

        Returns:
            A list of loaded plugins

        Raises:
            PluginDependencyError: If a required dependency is missing and check_dependencies is True
        """
        group_name = 'environment'
        plugin_types: list[PluginInformation[Environment]] = []

        # Filter entries by type
        for entry_point in list(metadata.entry_points(group=f'porringer.{group_name}')):
            try:
                loaded_type = entry_point.load()
            except ModuleNotFoundError as e:
                self.logger.warning(f"Plugin '{entry_point.name}' could not be loaded: {e}. Skipping")
                continue

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
                self.logger.debug(f'{group_name} plugin found: {canonicalized.name}')
                plugin_types.append(PluginInformation(loaded_type, entry_point.dist))

        if check_dependencies:
            plugin_types = self._resolve_dependencies(plugin_types)

        return plugin_types

    def _resolve_dependencies(
        self, plugins: list[PluginInformation[Environment]]
    ) -> list[PluginInformation[Environment]]:
        """Resolves plugin dependencies, filtering out plugins with unmet dependencies.

        Args:
            plugins: List of discovered plugins

        Returns:
            Filtered list of plugins with satisfied dependencies

        Raises:
            PluginDependencyError: If a required dependency is missing
        """
        # Build a set of available plugin names
        available_plugins: set[str] = set()
        for plugin_info in plugins:
            canonicalized = canonicalize_type(plugin_info.type)
            available_plugins.add(canonicalized.name)

        resolved_plugins: list[PluginInformation[Environment]] = []

        for plugin_info in plugins:
            plugin_name = canonicalize_type(plugin_info.type).name
            dependencies = plugin_info.type.dependencies()
            can_load = True

            for dep in dependencies:
                # Skip dependencies that don't apply to the current platform
                if not dep.is_applicable():
                    self.logger.debug(
                        f"Plugin '{plugin_name}' dependency on '{dep.plugin}' "
                        f'skipped (not applicable to current platform)'
                    )
                    continue

                if dep.plugin not in available_plugins:
                    if dep.required:
                        self.logger.error(f"Plugin '{plugin_name}' requires '{dep.plugin}' but it is not available")
                        raise PluginDependencyError(plugin_name, dep.plugin)
                    else:
                        self.logger.warning(
                            f"Plugin '{plugin_name}' has optional dependency on '{dep.plugin}' which is not available"
                        )
                else:
                    self.logger.debug(f"Plugin '{plugin_name}' dependency on '{dep.plugin}' satisfied")

            if can_load:
                resolved_plugins.append(plugin_info)

        return resolved_plugins

    @staticmethod
    def get_plugin_dependencies(plugin_type: type[Environment]) -> list[PluginDependency]:
        """Gets the applicable dependencies for a plugin on the current platform.

        Args:
            plugin_type: The plugin type to get dependencies for

        Returns:
            List of applicable dependencies
        """
        all_deps = plugin_type.dependencies()
        return [dep for dep in all_deps if dep.is_applicable()]

    @staticmethod
    def build_environment(environment_type: PluginInformation[Environment]) -> Environment:
        """Constructs a single environment from input type

        Args:
            environment_type: The type to construct

        Returns:
            The instantiated environment
        """
        pluginVersion = Version(environment_type.distribution.version)
        pluginDistribution = Distribution(version=pluginVersion)
        parameters = PluginParameters(distribution=pluginDistribution)

        return environment_type.type(parameters)

    @staticmethod
    def build_environments(environment_types: list[PluginInformation[Environment]]) -> list[Environment]:
        """Constructs environments from input types

        Args:
            environment_types: The types to construct

        Returns:
            The instantiated environments
        """
        environments: list[Environment] = []

        for environment_type in environment_types:
            environments.append(Builder.build_environment(environment_type))

        return environments
