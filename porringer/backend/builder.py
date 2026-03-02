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

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext, RuntimeProvider
from porringer.core.schema import Distribution, Plugin, PluginDependency, PluginParameters

logger = logging.getLogger(__name__)


@dataclass(slots=True)
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
                logger.warning("Plugin '%s' could not be loaded: %s. Skipping", entry_point.name, e)
                continue

            plugin_name = str(canonicalize_name(entry_point.name))

            if entry_point.dist is None:
                logger.warning("Plugin '%s' is not installed. Skipping", plugin_name)
                continue

            if not issubclass(loaded_type, base_class):
                logger.warning("Incompatible plugin '%s' — expected a '%s' subclass", plugin_name, group)
            else:
                logger.debug('%s plugin found: %s', group, plugin_name)
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

        Plugins whose required dependencies are not satisfied are
        silently excluded from the returned list.  Optional
        dependencies that are missing produce a warning but do not
        prevent the plugin from being included.

        Args:
            plugins: List of discovered plugins

        Returns:
            Filtered list of plugins with satisfied dependencies
        """
        # Build a set of available plugin names (PEP 503 normalised)
        available_plugins = {info.name for info in plugins}

        resolved_plugins: list[PluginInformation[T]] = []

        for plugin_info in plugins:
            plugin_name = plugin_info.name
            dependencies = plugin_info.type.dependencies()
            has_unmet_required = False

            for dep in dependencies:
                # Skip dependencies that don't apply to the current platform
                if not dep.is_applicable():
                    logger.debug(
                        "Plugin '%s' dependency on '%s' skipped (not applicable to current platform)",
                        plugin_name,
                        dep.plugin,
                    )
                    continue

                if str(canonicalize_name(dep.plugin)) not in available_plugins:
                    if dep.required:
                        logger.warning(
                            "Plugin '%s' requires '%s' but it is not available — skipping",
                            plugin_name,
                            dep.plugin,
                        )
                        has_unmet_required = True
                        break
                    else:
                        logger.warning(
                            "Plugin '%s' has optional dependency on '%s' which is not available",
                            plugin_name,
                            dep.plugin,
                        )
                else:
                    logger.debug("Plugin '%s' dependency on '%s' satisfied", plugin_name, dep.plugin)

            if not has_unmet_required:
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

    # ------------------------------------------------------------------
    # Runtime context resolution
    # ------------------------------------------------------------------

    @staticmethod
    async def resolve_runtime_context(environments: dict[str, Environment]) -> RuntimeContext:
        """Build a :class:`RuntimeContext` from available runtime providers.

        Scans *environments* for :class:`RuntimeProvider` instances that
        are supported and available on the current system, queries each
        for available runtime tags via
        :meth:`~RuntimeProvider.available_tags`, picks the highest
        version per runtime *kind*, and resolves its executable path.

        Unlike the sync pipeline's ``_propagate_runtime()`` (which
        operates on ``SetupAction`` objects), this method is decoupled
        from the action graph so it can be used by the query path
        (``list_packages``, ``build_plugin_info``, ``uninstall``,
        etc.).

        ``available_tags()`` deliberately includes runtimes that were
        not installed by the provider itself (e.g. python.org or
        Microsoft Store installs visible to the ``py`` launcher) so
        that downstream ``RuntimeConsumer`` plugins can still target
        the correct interpreter.

        Args:
            environments: Name-keyed dict of plugin instances
                (typically from ``_discover_environments()``).

        Returns:
            A ``RuntimeContext`` populated with resolved executables.
            May be empty when no provider or runtime is available.
        """
        ctx = RuntimeContext()

        for name, env in environments.items():
            if not isinstance(env, RuntimeProvider):
                continue
            if not env.is_supported() or not env.is_available():
                logger.debug("RuntimeProvider '%s' is not available; skipping", name)
                continue

            kind = env.provided_runtime_kind()
            if kind in ctx.executables:
                # Already resolved this kind from a previous provider
                continue

            try:
                tags = await env.available_tags()
            except Exception:
                logger.debug("Failed to list available tags for provider '%s'", name, exc_info=True)
                continue

            if not tags:
                logger.debug("RuntimeProvider '%s' reports no available tags", name)
                continue

            # Sort by Version descending to resolve the highest available runtime
            sorted_tags = sorted(
                tags,
                key=lambda tag: Version(tag) if tag else Version('0'),
                reverse=True,
            )

            for tag in sorted_tags:
                try:
                    executable = await env.resolve_executable(tag)
                except Exception:
                    logger.debug(
                        "resolve_executable failed for '%s' tag '%s'",
                        name,
                        tag,
                        exc_info=True,
                    )
                    continue
                if executable is not None:
                    ctx.executables[kind] = executable
                    logger.debug(
                        "Resolved runtime '%s' via provider '%s': tag=%s path=%s",
                        kind,
                        name,
                        tag,
                        executable,
                    )
                    break  # One executable per kind is sufficient
            else:
                logger.debug("RuntimeProvider '%s' could not resolve any executable", name)

        logger.debug(
            'resolve_runtime_context complete: %s',
            {k: str(v) for k, v in ctx.executables.items()} if ctx.executables else '<empty>',
        )
        return ctx
