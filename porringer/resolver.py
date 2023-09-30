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

    return Configuration()
