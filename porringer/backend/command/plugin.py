"""Yes"""


from logging import Logger

from porringer.schema import ListPluginResults


def list_plugins(logger: Logger) -> list[ListPluginResults]:
    """_summary_

    Args:
        logger: _description_

    Returns:
        _description_
    """

    logger.info("Listing plugins")
    return []


def update_plugins(logger: Logger) -> None:
    """_summary_

    Args:
        logger: _description_
    """

    logger.info("Updating plugins")
