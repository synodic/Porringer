"""API for Porringer"""

from porringer.resolver import resolve_configuration
from porringer.schema import (
    CheckPorringerParameters,
    Configuration,
    GlobalConfiguration,
    ListPluginsParameters,
    LocalConfiguration,
    Parameters,
    UpdatePorringerParameters,
)


class API:
    """_summary_"""

    def __init__(self, local_configuration: LocalConfiguration, parameters: Parameters) -> None:
        self.configuration: Configuration = resolve_configuration(local_configuration, GlobalConfiguration())
        self.parameters = parameters

    def update_porringer(self, parameters: UpdatePorringerParameters) -> None:
        """_summary_

        Args:
            parameters: _description_

        Raises:
            NotImplementedError: _description_
        """

    def check_porringer(self, parameters: CheckPorringerParameters) -> None:
        """_summary_

        Args:
            parameters: _description_

        Raises:
            NotImplementedError: _description_
        """

    def list_plugins(self, parameters: ListPluginsParameters) -> None:
        """_summary_

        Args:
            parameters: _description_
        """
