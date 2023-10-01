"""Resolves"""

from porringer.backend.schema import Configuration, GlobalConfiguration
from porringer.schema import LocalConfiguration


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
    local_configuration.cache_directory.mkdir(parents=True, exist_ok=True)

    global_configuration.config_directory.mkdir(parents=True, exist_ok=True)
    global_configuration.data_directory.mkdir(parents=True, exist_ok=True)

    return Configuration(
        cache_directory=local_configuration.cache_directory,
        config_directory=global_configuration.config_directory,
        data_directory=global_configuration.data_directory,
    )
