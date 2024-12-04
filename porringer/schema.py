"""Schema"""

from dataclasses import dataclass
from importlib.metadata import Distribution
from logging import Logger
from pathlib import Path

from packaging.version import Version
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field


class UpdatePorringerParameters(BaseModel):
    """TODO"""


class CheckPorringerParameters(BaseModel):
    """TODO"""


class ListPluginsParameters(BaseModel):
    """TODO"""

    pattern: str = Field(default='*', description='The pattern to match against')


class UpdatePluginsParameters(BaseModel):
    """TODO"""


@dataclass
class ListPluginResults:
    """_summary_

    Args:
        BaseModel: _description_
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
