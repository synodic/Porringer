"""Builder — generic plugin discovery and construction.

Provides `Builder.find_plugins()` and `Builder.build_plugins()`
for discovering and instantiating plugins from any entry-point group
without per-kind boilerplate.
"""

import logging
from dataclasses import dataclass
from importlib import metadata
from importlib.metadata import Distribution as MetadataDistribution

from packaging.utils import canonicalize_name
from packaging.version import Version

from porringer.core.schema import Distribution, Plugin, PluginDependency, PluginParameters
from porringer.utility.exception import PluginDependencyError

logger = logging.getLogger(__name__)


@dataclass
class PluginInformation[P]:
    """Gathered information about available plugins."""

    type: type[P]
    distribution: MetadataDistribution
    name: str


class Builder:
    """Helper class for building Porringer projects"""

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

        entry_points = list(metadata.entry_points(group=f'porringer.{group}'))
        logger.debug('Entry points for porringer.%s: %s', group, [ep.name for ep in entry_points])

        for entry_point in entry_points:
            try:
                loaded_type = entry_point.load()
            except Exception as e:
                logger.warning(f"Plugin '{entry_point.name}' could not be loaded: {e}. Skipping")
                continue

            plugin_name = str(canonicalize_name(entry_point.name))

            if entry_point.dist is None:
                logger.error(f"Plugin '{plugin_name}' is not installed. Skipping")
                continue

            if not issubclass(loaded_type, base_class):
                logger.warning(
                    f"Found incompatible plugin. The '{plugin_name}' plugin must be an instance of '{group}'"
                )
            else:
                logger.debug(f'{group} plugin found: {plugin_name}')
                plugin_types.append(PluginInformation(loaded_type, entry_point.dist, plugin_name))

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
        # Build a set of available plugin names (PEP 503 normalised)
        available_plugins = {info.name for info in plugins}

        resolved_plugins: list[PluginInformation[T]] = []

        for plugin_info in plugins:
            plugin_name = plugin_info.name
            dependencies = plugin_info.type.dependencies()

            for dep in dependencies:
                # Skip dependencies that don't apply to the current platform
                if not dep.is_applicable():
                    logger.debug(
                        f"Plugin '{plugin_name}' dependency on '{dep.plugin}' "
                        f'skipped (not applicable to current platform)'
                    )
                    continue

                if str(canonicalize_name(dep.plugin)) not in available_plugins:
                    if dep.required:
                        logger.error(f"Plugin '{plugin_name}' requires '{dep.plugin}' but it is not available")
                        raise PluginDependencyError(plugin_name, dep.plugin)
                    else:
                        logger.warning(
                            f"Plugin '{plugin_name}' has optional dependency on '{dep.plugin}' which is not available"
                        )
                else:
                    logger.debug(f"Plugin '{plugin_name}' dependency on '{dep.plugin}' satisfied")

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
