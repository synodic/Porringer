"""Resolves"""

from porringer.backend.schema import GlobalConfiguration, ResolvedDirectories
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import LocalConfiguration, PluginInfo


def resolve_configuration(
    local_configuration: LocalConfiguration, global_configuration: GlobalConfiguration
) -> ResolvedDirectories:
    """Resolves the configuration.

    Args:
        local_configuration: The local configuration.
        global_configuration: The global configuration.

    Returns:
        The resolved configuration.
    """
    local_configuration.cache_directory.mkdir(parents=True, exist_ok=True)

    global_configuration.config_directory.mkdir(parents=True, exist_ok=True)
    global_configuration.data_directory.mkdir(parents=True, exist_ok=True)

    return ResolvedDirectories(
        cache_directory=local_configuration.cache_directory,
        config_directory=global_configuration.config_directory,
        data_directory=global_configuration.data_directory,
    )


def build_plugin_info(
    plugins: dict[str, Plugin] | list[Plugin],
    kinds: list[PluginKind] | None = None,
) -> list[PluginInfo]:
    """Build metadata for discovered plugins, optionally filtered by kind.

    Accepts any `Plugin` instance (`Environment`, `ProjectEnvironment`,
    `ScmEnvironment`).  The `tool_version` field is populated for any
    `ToolBasedPlugin` that reports itself as available.

    Args:
        plugins: Discovered plugin instances, either as a name-keyed dict
            or a flat list (names taken from the dict keys when available).
        kinds: Only include plugins matching these kinds.  `None` returns all.

    Returns:
        A filtered list of plugin metadata.
    """
    results: list[PluginInfo] = []

    items: list[tuple[str | None, Plugin]]
    if isinstance(plugins, dict):
        items = [(name, plugin) for name, plugin in plugins.items()]
    else:
        items = [(None, plugin) for plugin in plugins]

    for name, plugin in items:
        plugin_type = type(plugin)
        kind = plugin_type.plugin_kind()

        if kinds and kind not in kinds:
            continue

        installed = plugin.is_available()

        tool_version = None
        if installed and isinstance(plugin, ToolBasedPlugin):
            tool_version = plugin.tool_version()

        # Use the entry-point name when available; fall back to empty string
        plugin_name = name if name is not None else ''

        results.append(
            PluginInfo(
                name=plugin_name,
                kind=kind,
                version=plugin.distribution.version,
                installed=installed,
                tool_version=tool_version,
            )
        )

    return results
