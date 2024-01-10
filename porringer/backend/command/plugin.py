"""Yes"""

from logging import Logger

from porringer.schema import ListPluginResults, ListPluginsParameters


def list_plugins(parameters: ListPluginsParameters, logger: Logger) -> list[ListPluginResults]:
    """_summary_

    Args:
        parameters: The list command parameters.
        logger: The logger.

    Returns:
        A list of registered plugins.
    """

    logger.info("Listing plugins")
    return []


def update_plugins(logger: Logger) -> None:
    """_summary_

    Args:
        logger: _description_
    """

    logger.info("Updating plugins")
