"""Resolves"""

from porringer.backend.schema import GlobalConfiguration, ResolvedDirectories
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import LocalConfiguration, PluginInfo
from porringer.utility.utility import canonicalize_type


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
    plugins: list[Plugin],
    kinds: list[PluginKind] | None = None,
) -> list[PluginInfo]:
    """Build metadata for discovered plugins, optionally filtered by kind.

    Accepts any `Plugin` instance (`Environment`, `ProjectEnvironment`,
    `ScmEnvironment`).  The `tool_version` field is populated for any
    `ToolBasedPlugin` that reports itself as available.

    Args:
        plugins: Discovered plugin instances from all groups.
        kinds: Only include plugins matching these kinds.  `None` returns all.

    Returns:
        A filtered list of plugin metadata.
    """
    results: list[PluginInfo] = []

    for plugin in plugins:
        plugin_type = type(plugin)
        kind = plugin_type.plugin_kind()

        if kinds and kind not in kinds:
            continue

        canonicalized = canonicalize_type(plugin_type)
        installed = plugin.is_available()

        tool_version = None
        if installed and isinstance(plugin, ToolBasedPlugin):
            tool_version = plugin.tool_version()

        results.append(
            PluginInfo(
                name=canonicalized.name,
                kind=kind,
                version=plugin.distribution.version,
                installed=installed,
                tool_version=tool_version,
            )
        )

    return results
