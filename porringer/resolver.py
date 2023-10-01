"""Resolves"""

from porringer.schema import Configuration, GlobalConfiguration, LocalConfiguration


def resolve_configuration(
    local_configuration: LocalConfiguration, global_configuration: GlobalConfiguration
) -> Configuration:
    """_summary_

    Args:
        local_configuration: _description_
        global_configuration: _description_

    Returns:
        _description_
    """
    local_configuration.cache_directory.mkdir(parents=True)

    global_configuration.config_directory.mkdir(parents=True)
    global_configuration.data_directory.mkdir(parents=True)

    return Configuration(
        cache_directory=local_configuration.cache_directory,
        config_directory=global_configuration.config_directory,
        data_directory=global_configuration.data_directory,
    )
