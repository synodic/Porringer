"""API for Porringer"""

from synodic_utilities.subprocess import call

from porringer.backend.version import is_pipx_installation
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
        if is_pipx_installation():
            call(["pipx", "upgrade", "porringer"], self.parameters.logger)
        else:
            raise NotImplementedError()

    def check_porringer(self, parameters: CheckPorringerParameters) -> None:
        """_summary_

        Args:
            parameters: _description_

        Raises:
            NotImplementedError: _description_
        """
        if is_pipx_installation():
            call(["pipx", "upgrade", "porringer"], self.parameters.logger)
        else:
            raise NotImplementedError()

    def list_plugins(self, parameters: ListPluginsParameters) -> None:
        """_summary_

        Args:
            parameters: _description_
        """
        self.parameters.logger.info("Listing plugins")
