"""Update source adapters for checking available versions."""

from abc import ABC, abstractmethod
from logging import Logger

from porringer.schema import CheckUpdateParameters, UpdateInfo


class UpdateSourceAdapter(ABC):
    """Base class for update source adapters."""

    def __init__(self, logger: Logger) -> None:
        """Initialize the adapter with a logger.

        Args:
            logger: Logger instance for logging.
        """
        self.logger = logger

    @abstractmethod
    def check(self, parameters: CheckUpdateParameters) -> UpdateInfo:
        """Check for updates from this source.

        Args:
            parameters: The check parameters.

        Returns:
            UpdateInfo with version comparison results.
        """
        raise NotImplementedError
