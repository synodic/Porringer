"""Data schemas for console commands"""

from dataclasses import dataclass
from typing import LiteralString

from pydantic import BaseModel, ConfigDict
from rich.console import Console

from porringer.schema import LocalConfiguration

MAX_VERBOSITY_LEVEL = 3


@dataclass
class LogLevel:
    """Log level metadata"""

    name: LiteralString
    colour: str


LOG_LEVELS: list[LogLevel] = [
    LogLevel(name='ERROR', colour='red'),
    LogLevel(name='WARNING', colour='yellow'),
    LogLevel(name='INFO', colour='white'),
    LogLevel(name='DEBUG', colour='bright_white'),
]


class Configuration(BaseModel):
    """Configuration object for the CLI"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    console: Console = Console()
    local_configuration: LocalConfiguration = LocalConfiguration()
    debug: bool = False
    verbosity: int = 0
