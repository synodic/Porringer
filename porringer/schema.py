"""Schema"""

from dataclasses import dataclass
from importlib.metadata import Distribution
from logging import Logger
from pathlib import Path

from packaging.version import Version
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field


class UpdatePorringerParameters(BaseModel):
    """Parameters for updating the Porringer application."""


class CheckPorringerParameters(BaseModel):
    """Parameters for checking the Porringer application status."""


class ListPluginsParameters(BaseModel):
    """Parameters for listing available plugins."""

    pattern: str = Field(default='*', description='The pattern to match against')


class UpdatePluginsParameters(BaseModel):
    """Parameters for updating plugins."""


@dataclass
class ListPluginResults:
    """Results of listing plugins.

    Args:
        name: The name of the plugin.
        version: The version of the plugin.
    """

    name: str
    version: Version


@dataclass
class Parameters:
    """Resolved configuration"""

    logger: Logger


@dataclass
class PluginInformation[Plugin]:
    """Gathered information about available plugins"""

    type: type[Plugin]
    distribution: Distribution


class LocalConfiguration(BaseModel):
    """Configuration provided by the application running Porringer"""

    cache_directory: Path = Field(
        default=Path(user_cache_dir('porringer', 'synodic')), description='The application cache path '
    )
