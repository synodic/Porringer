"""Backend schema"""

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, DirectoryPath, Field


class GlobalConfiguration(BaseModel):
    """Global configuration that Porringer manages"""

    config_directory: Path = Field(
        default=Path(user_config_dir('porringer', 'synodic')),
        description='The configuration directory',
    )

    data_directory: Path = Field(
        default=Path(user_data_dir('porringer', 'synodic')),
        description='The data directory',
    )


class Configuration(BaseModel):
    """Resolved configuration"""

    cache_directory: DirectoryPath
    config_directory: DirectoryPath
    data_directory: DirectoryPath
