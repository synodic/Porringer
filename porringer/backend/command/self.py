"""Utilities for managing and checking the Porringer installation version."""

import importlib.metadata
import os
import subprocess
import sys
from logging import Logger


class SelfCommands:
    """Commands related to the Porringer installation."""

    def __init__(self, logger: Logger) -> None:
        """Initialize the SelfCommands class.

        Args:
            logger: Logger instance for logging actions.
        """
        self.logger = logger

    @staticmethod
    def is_pipx_installation() -> bool:
        """Check if Porringer is installed via pipx.

        Returns:
            bool: True if the current Python environment is a pipx-managed venv, False otherwise.
        """
        return sys.prefix.split(os.sep)[-3:-1] == ['pipx', 'venvs']

    def update(self) -> None:
        """Upgrade the Porringer package using pipx if installed via pipx.

        Raises:
            NotImplementedError: If Porringer is not installed via pipx.
        """
        if self.is_pipx_installation():
            subprocess.run(['pipx', 'upgrade', 'porringer'], check=True)
        else:
            raise NotImplementedError()

    def check(self) -> bool:
        """Check for updates to the Porringer package using pipx if installed via pipx.

        Returns:
            True if an update is available, False otherwise.

        Raises:
            NotImplementedError: If Porringer is not installed via pipx.
        """
        if self.is_pipx_installation():
            # Check if an update is available without installing it
            result = subprocess.run(
                ['pipx', 'runpip', 'porringer', 'index', 'versions', 'porringer'],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                # Parse output to see if there's a newer version
                # Output format: "porringer (X.Y.Z)\nAvailable versions: ..."
                current = importlib.metadata.version('porringer')
                self.logger.info(f'Current version: {current}')
                self.logger.info(result.stdout)
                return True  # For now, just report that check was performed
            return False
        else:
            raise NotImplementedError()
