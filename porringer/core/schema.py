"""Schema for Porringer"""

from abc import abstractmethod
from typing import NewType, Protocol, TypeVar

from packaging.version import Version
from pydantic import BaseModel


class PorringerModel(BaseModel):
    """The base model to use for all Porringer models"""

    model_config = {'populate_by_name': False, 'arbitrary_types_allowed': True}


PackageName = NewType('PackageName', str)


class Package(PorringerModel):
    """Package definition"""

    name: PackageName
    version: str


class SupportedFeatures(PorringerModel):
    """Plugin feature support"""


class Distribution(PorringerModel):
    """Data that describes the distribution of the plugin"""

    version: Version


class PluginParameters(PorringerModel):
    """Generic plugin parameters that will be used to construct a Plugin instance"""

    distribution: Distribution


class Information(PorringerModel):
    """Plugin information that complements the packaged project metadata"""


class Plugin(Protocol):
    """Porringer plugin"""

    _distribution: Distribution

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the plugin"""
        self._distribution = parameters.distribution

    @staticmethod
    @abstractmethod
    def features() -> SupportedFeatures:
        """Broadcasts the shared features of the plugin to Porringer

        Returns:
            The supported features
        """
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def information() -> Information:
        """Retrieves plugin information that complements the packaged project metadata

        Returns:
            The plugin's information
        """
        raise NotImplementedError

    @property
    def distribution(self) -> Distribution:
        """Retrieves plugin information that complements the packaged project metadata

        Returns:
            The plugin's information
        """
        return self._distribution


PluginT = TypeVar('PluginT', bound=Plugin)
