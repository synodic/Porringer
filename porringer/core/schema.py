"""Schema for Porringer"""

import sys
from abc import abstractmethod
from typing import NewType, Protocol, TypeVar

from packaging.version import Version
from pydantic import BaseModel, Field


class PorringerModel(BaseModel):
    """The base model to use for all Porringer models"""

    model_config = {'populate_by_name': False, 'arbitrary_types_allowed': True}


class PlatformScoped(BaseModel):
    """Mixin for models that can be scoped to specific platforms.

    When ``platforms`` is empty the entry applies everywhere.
    Otherwise, the entry is only applicable when ``sys.platform``
    appears in the list.
    """

    platforms: list[str] = Field(
        default_factory=list,
        description='List of platforms where this entry applies (e.g., ["win32"]). Empty means all platforms.',
    )

    def is_applicable(self) -> bool:
        """Check if this entry applies to the current platform.

        Returns:
            True if the entry applies to the current platform
        """
        if not self.platforms:
            return True
        return sys.platform in self.platforms


PackageName = NewType('PackageName', str)


class PluginDependency(PorringerModel, PlatformScoped):
    """Defines a dependency on another plugin"""

    plugin: str = Field(description='The name of the required plugin')
    required: bool = Field(default=True, description='Whether this dependency is required (True) or optional (False)')


class Package(PorringerModel):
    """Package definition"""

    name: PackageName
    version: str | None = None


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

    @staticmethod
    def dependencies() -> list[PluginDependency]:
        """Declares plugin dependencies on other plugins.

        Dependencies can be platform-specific and either required or optional.
        Override this method to declare dependencies for your plugin.

        Returns:
            A list of plugin dependencies
        """
        return []

    @property
    def distribution(self) -> Distribution:
        """Retrieves plugin information that complements the packaged project metadata

        Returns:
            The plugin's information
        """
        return self._distribution


PluginT = TypeVar('PluginT', bound=Plugin)
