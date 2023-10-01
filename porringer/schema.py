"""Schema"""
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir
from pydantic import BaseModel, DirectoryPath, Field


class UpdatePorringerParameters(BaseModel):
    """TODO"""


class CheckPorringerParameters(BaseModel):
    """TODO"""


class ListPluginsParameters(BaseModel):
    """TODO"""


@dataclass
class Parameters:
    """Resolved configuration"""

    logger: Logger


class Configuration(BaseModel):
    """Resolved configuration"""

    cache_directory: DirectoryPath
    config_directory: DirectoryPath
    data_directory: DirectoryPath


class LocalConfiguration(BaseModel):
    """Configuration provided by the application running Porringer"""

    cache_directory: Path = Field(
        default=Path(user_cache_dir("porringer", "synodic")), description="The application cache path "
    )


class GlobalConfiguration(BaseModel):
    """Global configuration that Porringer manages"""

    config_directory: Path = Field(
        default=Path(user_config_dir("porringer", "synodic")),
        description="The configuration directory",
    )

    data_directory: Path = Field(
        default=Path(user_data_dir("porringer", "synodic")),
        description="The data directory",
    )
