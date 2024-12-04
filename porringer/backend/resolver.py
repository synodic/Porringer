"""Resolves"""

from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.core.plugin_schema.environment import Environment
from porringer.schema import ListPluginResults, LocalConfiguration
from porringer.utility.utility import canonicalize_type


def resolve_configuration(
    local_configuration: LocalConfiguration, global_configuration: GlobalConfiguration
) -> Configuration:
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

    return Configuration(
        cache_directory=local_configuration.cache_directory,
        config_directory=global_configuration.config_directory,
        data_directory=global_configuration.data_directory,
    )


def resolve_list_plugins_parameters(environment: list[Environment]) -> list[ListPluginResults]:
    """Resolves the list plugins parameters.

    Args:
        environment: The environment.

    Returns:
        A list of plugin metadata.
    """
    plugin_metadata: list[ListPluginResults] = []

    for plugin in environment:
        canonicalized = canonicalize_type(type(plugin))

        resolved_metadata = ListPluginResults(canonicalized.name, plugin.distribution.version)
        plugin_metadata.append(resolved_metadata)

    return plugin_metadata
