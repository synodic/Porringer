"""Utilities for managing and checking the Porringer installation version."""

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

    def is_pipx_installation(self) -> bool:
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

    def check(self) -> None:
        """Check for updates to the Porringer package using pipx if installed via pipx.

        Raises:
            NotImplementedError: If Porringer is not installed via pipx.
        """
        if self.is_pipx_installation():
            subprocess.run(['pipx', 'upgrade', 'porringer'], check=True)
        else:
            raise NotImplementedError()
