"""Plugin integration for plugin."""

"""Git SCM plugin implementation."""

import asyncio
import logging
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Ecosystem

logger = logging.getLogger(__name__)


class GitScm(ScmEnvironment):
    """SCM environment plugin for Git.

    Provides clone and presence-check operations using the `git`
    command-line tool.
    """

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
    async def clone(self, url: str, destination: Path, *, dry: bool = False) -> bool:
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

        return await self._run_bool_command(['git', 'clone', url, str(destination)], label='clone')

    @override
    async def get_remote_urls(self, destination: Path) -> dict[str, str]:
        """Return all remote fetch URLs for the Git repository at *destination*.

        Parses ``git remote -v`` output, collecting only fetch URLs.

        Args:
            destination: Local path of an existing Git clone.

        Returns:
            A mapping of remote name to fetch URL.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                'git',
                '-C',
                str(destination),
                'remote',
                '-v',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return {}
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
        except FileNotFoundError, OSError:
            return {}

        _min_remote_fields = 3  # "<name>\t<url> (fetch|push)" → at least 3 tokens
        remotes: dict[str, str] = {}
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= _min_remote_fields and parts[-1] == '(fetch)':
                remotes[parts[0]] = parts[1]
        return remotes

    @override
    async def find_repo_root(self, path: Path) -> Path | None:
        """Find the Git repository root that contains *path*.

        Uses ``git rev-parse --show-toplevel`` to locate the root.

        Args:
            path: A filesystem path that may be inside a Git repository.

        Returns:
            The repository root directory, or ``None`` if *path* is
            not inside a Git repository.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                'git',
                '-C',
                str(path),
                'rev-parse',
                '--show-toplevel',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                stdout = stdout_bytes.decode('utf-8', errors='replace').strip() if stdout_bytes else ''
                return Path(stdout)
        except FileNotFoundError, OSError:
            pass
        return None
