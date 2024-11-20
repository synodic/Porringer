"""Yes"""

from logging import Logger

from porringer.backend.builder import Builder
from porringer.backend.resolver import resolve_list_plugins_parameters
from porringer.schema import ListPluginResults, ListPluginsParameters


def list_plugins(parameters: ListPluginsParameters, logger: Logger) -> list[ListPluginResults]:
    """_summary_

    Args:
        parameters: The list command parameters.
        logger: The logger.

    Returns:
        A list of registered plugins.
    """

    logger.info('Listing plugins')

    builder = Builder(logger)

    environment_types = builder.find_environments()

    environments = builder.build_environments(environment_types)

    return resolve_list_plugins_parameters(environments)


def update_plugins(logger: Logger) -> None:
    """_summary_

    Args:
        logger: _description_
    """

    logger.info('Updating plugins')
