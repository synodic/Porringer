"""Git SCM plugin implementation."""

import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Ecosystem, PluginParameters

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
    def get_remote_urls(self, destination: Path) -> dict[str, str]:
        """Return all remote fetch URLs for the Git repository at *destination*.

        Parses ``git remote -v`` output, collecting only fetch URLs.

        Args:
            destination: Local path of an existing Git clone.

        Returns:
            A mapping of remote name to fetch URL.
        """
        try:
            result = subprocess.run(
                ['git', '-C', str(destination), 'remote', '-v'],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                return {}
        except FileNotFoundError, subprocess.SubprocessError:
            return {}

        _min_remote_fields = 3  # "<name>\t<url> (fetch|push)" → at least 3 tokens
        remotes: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= _min_remote_fields and parts[-1] == '(fetch)':
                remotes[parts[0]] = parts[1]
        return remotes

    @override
    def find_repo_root(self, path: Path) -> Path | None:
        """Find the Git repository root that contains *path*.

        Uses ``git rev-parse --show-toplevel`` to locate the root.

        Args:
            path: A filesystem path that may be inside a Git repository.

        Returns:
            The repository root directory, or ``None`` if *path* is
            not inside a Git repository.
        """
        try:
            result = subprocess.run(
                ['git', '-C', str(path), 'rev-parse', '--show-toplevel'],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except FileNotFoundError, subprocess.SubprocessError:
            pass
        return None
