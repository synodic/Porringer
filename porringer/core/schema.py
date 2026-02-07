"""Schema for Porringer"""

import sys
from abc import abstractmethod
from typing import Protocol, TypeVar

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import Version
from pydantic import BaseModel, Field, model_validator


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


class PackageRef(PorringerModel):
    """A package reference with an optional version constraint.

    Represents a package identifier that may include a PEP 440 version specifier
    (e.g. ``"ruff>=0.8.0"``, ``"pydantic>=2,<3"``). A bare name such as ``"pytest"``
    is also valid (constraint will be ``None``).

    Can be constructed in several ways::

        PackageRef(name='ruff', constraint='>=0.8.0')
        PackageRef('ruff>=0.8.0')  # via model validator (string coercion)
    """

    model_config = {'frozen': True}

    name: str = Field(description='The bare, canonical package name')
    constraint: str | None = Field(default=None, description='PEP 440 version specifier (e.g. ">=0.8.0")')

    @model_validator(mode='before')
    @classmethod
    def _coerce_string(cls, data: str | dict) -> dict:  # type: ignore[type-arg]
        """Accept plain strings and auto-parse them into name + constraint."""
        if isinstance(data, str):
            return cls._split_spec(data)
        return data

    @staticmethod
    def _split_spec(spec: str) -> dict[str, str | None]:
        """Decompose a specifier string into name and constraint components."""
        try:
            req = Requirement(spec)
            constraint = str(req.specifier) if req.specifier else None
            return {'name': req.name, 'constraint': constraint}
        except InvalidRequirement as exc:
            raise ValueError(f'Invalid package specifier: {spec!r}') from exc

    @property
    def specifier(self) -> str:
        """The full specifier string (name + constraint) suitable for CLI commands.

        Examples:
            ``"ruff>=0.8.0"``, ``"pytest"``
        """
        if self.constraint:
            return f'{self.name}{self.constraint}'
        return self.name

    def __str__(self) -> str:
        """Return the full specifier string."""
        return self.specifier


class PluginDependency(PorringerModel, PlatformScoped):
    """Defines a dependency on another plugin"""

    plugin: str = Field(description='The name of the required plugin')
    required: bool = Field(default=True, description='Whether this dependency is required (True) or optional (False)')


class Package(PorringerModel):
    """Package definition — represents an installed package identity."""

    name: str = Field(description='The bare package name')
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
