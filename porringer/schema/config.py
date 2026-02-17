"""Configuration schemas."""

from pathlib import Path

from platformdirs import user_cache_dir
from pydantic import BaseModel, Field


class LocalConfiguration(BaseModel):
    """Configuration provided by the application running Porringer"""

    cache_directory: Path = Field(
        default=Path(user_cache_dir('porringer', 'synodic')), description='The application cache path '
    )
