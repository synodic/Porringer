"""Git SCM plugin implementation."""

import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import PluginParameters

logger = logging.getLogger(__name__)


class GitScm(ScmEnvironment):
    """SCM environment plugin for Git.

    Provides clone and presence-check operations using the `git`
    command-line tool.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the Git SCM plugin.

        Args:
            parameters: Plugin parameters including distribution info.
        """
        super().__init__(parameters)

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Return the Git CLI executable name."""
        return 'git'

    @staticmethod
    @override
    def ecosystem() -> str:
        """Git belongs to the `git` ecosystem."""
        return 'git'

    @override
    def clone(self, url: str, destination: Path, *, dry: bool = False) -> bool:
        """Clone a Git repository from *url* into *destination*.

        Args:
            url: The repository URL to clone.
            destination: Local filesystem path for the clone.
            dry: If `True`, preview without modifying the filesystem.

        Returns:
            `True` on success, `False` on failure.
        """
        if dry:
            logger.info('Would clone %s into %s', url, destination)
            return True

        args = ['git', 'clone', url, str(destination)]
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return False
        except FileNotFoundError:
            logger.error('git not found on PATH')
            return False
        except subprocess.SubprocessError as e:
            logger.error('Failed to clone repository: %s', e)
            return False
        return True

    @override
    def is_cloned(self, url: str, destination: Path) -> bool:
        """Check whether a Git repository already exists at *destination*.

        A directory is considered cloned if it exists and contains a
        `.git` subdirectory.

        Args:
            url: The repository URL (unused — presence is path-based).
            destination: Expected local path for the clone.

        Returns:
            `True` if the repository is already present.
        """
        return destination.is_dir() and (destination / '.git').is_dir()
