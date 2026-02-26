"""Git SCM plugin implementation."""

import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Ecosystem, PluginParameters
from porringer.schema.execution import CloneStatus, CloneStatusKind

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
    def ecosystem() -> Ecosystem:
        """Git belongs to the `git` ecosystem."""
        return Ecosystem('git')

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

        return self._run_bool_command(['git', 'clone', url, str(destination)], label='clone')

    @override
    def is_cloned(self, url: str, destination: Path) -> CloneStatus:
        """Check whether a Git repository already exists at *destination*.

        A directory is considered cloned if it exists and contains a
        ``.git`` subdirectory **and** its ``origin`` remote URL matches
        *url* (after normalization).

        Args:
            url: The expected repository URL.
            destination: Expected local path for the clone.

        Returns:
            A `CloneStatus` indicating the result.
        """
        if not destination.is_dir() or not (destination / '.git').is_dir():
            return CloneStatus(kind=CloneStatusKind.MISSING)

        actual_url = self.get_remote_url(destination)
        if actual_url is None or not self.urls_match(url, actual_url):
            return CloneStatus(kind=CloneStatusKind.URL_MISMATCH, remote_url=actual_url)

        return CloneStatus(kind=CloneStatusKind.CLONED, remote_url=actual_url)

    @override
    def get_remote_url(self, destination: Path) -> str | None:
        """Return the remote origin URL for the Git repository at *destination*.

        Args:
            destination: Local path of an existing Git clone.

        Returns:
            The remote URL string, or ``None`` if unavailable.
        """
        try:
            result = subprocess.run(
                ['git', '-C', str(destination), 'remote', 'get-url', 'origin'],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except FileNotFoundError, subprocess.SubprocessError:
            pass
        return None
