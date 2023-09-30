"""Schema"""
from dataclasses import dataclass
from logging import Logger

from pydantic import BaseModel


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


class LocalConfiguration(BaseModel):
    """Configuration provided by the application running Porringer"""


class GlobalConfiguration(BaseModel):
    """Global configuration that Porringer manages"""
