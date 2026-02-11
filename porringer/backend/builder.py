"""Builder — generic plugin discovery and construction.

Provides `Builder.find_plugins()` and `Builder.build_plugins()`
for discovering and instantiating plugins from any entry-point group
without per-kind boilerplate.
"""

import logging
from importlib import metadata

from packaging.version import Version

from porringer.core.schema import Distribution, Plugin, PluginDependency, PluginParameters
from porringer.schema import PluginInformation
from porringer.utility.exception import PluginDependencyError
from porringer.utility.utility import canonicalize_type

logger = logging.getLogger(__name__)


class Builder:
    """Helper class for building Porringer projects"""

    def __init__(self) -> None:
        """Initializes the builder"""
        pass

    # ------------------------------------------------------------------
    # Generic discovery & construction
    # ------------------------------------------------------------------

    @staticmethod
    def find_plugins[T: Plugin](
        group: str,
        base_class: type[T],
        *,
        check_dependencies: bool = False,
    ) -> list[PluginInformation[T]]:
        """Search for registered plugins in an entry-point group.

        Scans `porringer.<group>` for classes that are subclasses of
        *base_class* and returns plugin-info wrappers for each.

        Args:
            group: Entry-point group suffix (e.g. `'environment'`).
            base_class: Expected base class; incompatible types are skipped.
            check_dependencies: If `True`, validates plugin dependencies
                and filters out plugins with unmet required dependencies.

        Returns:
            A list of discovered plugin information objects.
        """
        plugin_types: list[PluginInformation[T]] = []

        for entry_point in list(metadata.entry_points(group=f'porringer.{group}')):
            try:
                loaded_type = entry_point.load()
            except ModuleNotFoundError as e:
                logger.warning(f"Plugin '{entry_point.name}' could not be loaded: {e}. Skipping")
                continue

            canonicalized = canonicalize_type(loaded_type)

            if entry_point.dist is None:
                logger.error(f"Plugin '{canonicalized.name}' is not installed. Skipping")
                continue

            if not issubclass(loaded_type, base_class):
                logger.warning(
                    f"Found incompatible plugin. The '{canonicalized.name}' plugin must be an instance of '{group}'"
                )
            else:
                logger.debug(f'{group} plugin found: {canonicalized.name}')
                plugin_types.append(PluginInformation(loaded_type, entry_point.dist))

        if check_dependencies:
            plugin_types = Builder._resolve_dependencies(plugin_types)

        return plugin_types

    @staticmethod
    def build_plugin[T: Plugin](info: PluginInformation[T]) -> T:
        """Construct a single plugin instance from its discovery info.

        Args:
            info: The plugin information (type + distribution).

        Returns:
            The instantiated plugin.
        """
        plugin_version = Version(info.distribution.version)
        plugin_distribution = Distribution(version=plugin_version)
        parameters = PluginParameters(distribution=plugin_distribution)
        return info.type(parameters)

    @staticmethod
    def build_plugins[T: Plugin](infos: list[PluginInformation[T]]) -> list[T]:
        """Construct plugin instances from a list of discovery infos.

        Args:
            infos: The plugin information list.

        Returns:
            The instantiated plugins.
        """
        return [Builder.build_plugin(i) for i in infos]

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_dependencies[T: Plugin](
        plugins: list[PluginInformation[T]],
    ) -> list[PluginInformation[T]]:
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

        resolved_plugins: list[PluginInformation[T]] = []

        for plugin_info in plugins:
            plugin_name = canonicalize_type(plugin_info.type).name
            dependencies = plugin_info.type.dependencies()
            can_load = True

            for dep in dependencies:
                # Skip dependencies that don't apply to the current platform
                if not dep.is_applicable():
                    logger.debug(
                        f"Plugin '{plugin_name}' dependency on '{dep.plugin}' "
                        f'skipped (not applicable to current platform)'
                    )
                    continue

                if dep.plugin not in available_plugins:
                    if dep.required:
                        logger.error(f"Plugin '{plugin_name}' requires '{dep.plugin}' but it is not available")
                        raise PluginDependencyError(plugin_name, dep.plugin)
                    else:
                        logger.warning(
                            f"Plugin '{plugin_name}' has optional dependency on '{dep.plugin}' which is not available"
                        )
                else:
                    logger.debug(f"Plugin '{plugin_name}' dependency on '{dep.plugin}' satisfied")

            if can_load:
                resolved_plugins.append(plugin_info)

        return resolved_plugins

    @staticmethod
    def get_plugin_dependencies(plugin_type: type[Plugin]) -> list[PluginDependency]:
        """Gets the applicable dependencies for a plugin on the current platform.

        Args:
            plugin_type: The plugin type to get dependencies for

        Returns:
            List of applicable dependencies
        """
        all_deps = plugin_type.dependencies()
        return [dep for dep in all_deps if dep.is_applicable()]
