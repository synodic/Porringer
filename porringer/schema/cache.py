"""Data models and schemas for cache.

Local application configuration (cache directory).
"""

from pathlib import Path

from platformdirs import user_cache_dir
from pydantic import Field

from porringer.core.schema import PorringerModel


class LocalConfiguration(PorringerModel):
    """Configuration provided by the application running Porringer."""

    cache_directory: Path = Field(
        default=Path(user_cache_dir('porringer', 'synodic')), description='The application cache path '
    )
