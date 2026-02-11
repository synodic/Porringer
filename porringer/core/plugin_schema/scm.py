"""Plugin utilities for source-control management (SCM) environments.

An `ScmEnvironment` plugin wraps an SCM tool (e.g. Git) and
provides clone and presence-check operations.  The sync engine invokes
SCM actions after project sync but before post-sync commands, so that
cloned repositories are available for any post-sync scripts.
"""

import logging
from abc import abstractmethod
from pathlib import Path

from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import PluginKind, PluginParameters

logger = logging.getLogger(__name__)


class ScmEnvironment(ToolBasedPlugin):
    """Plugin definition for source-control management environments.

    Unlike `Environment`,
    which installs individual packages, an `ScmEnvironment` clones
    repositories using an SCM tool such as Git.

    Subclasses **must** override `tool_name()`, `clone()`,
    and `is_cloned()`.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the SCM environment plugin.

        Args:
            parameters: Plugin parameters including distribution info.
        """
        super().__init__(parameters)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def tool_name(cls) -> str:
        """Return the CLI executable name this plugin wraps.

        Used by `is_available()` to verify the tool is on PATH and
        by `clone_command()` to build the default command.
        """
        ...

    @abstractmethod
    def clone(self, url: str, destination: Path, *, dry: bool = False) -> bool:
        """Clone a repository from *url* into *destination*.

        Args:
            url: The repository URL to clone.
            destination: Local filesystem path for the clone.
            dry: If `True`, preview without modifying the filesystem.

        Returns:
            `True` on success, `False` on failure.
        """
        ...

    @abstractmethod
    def is_cloned(self, url: str, destination: Path) -> bool:
        """Check whether *url* has already been cloned to *destination*.

        Used by the skip logic to avoid re-cloning repositories that are
        already present.

        Args:
            url: The repository URL.
            destination: Expected local path for the clone.

        Returns:
            `True` if the repository is already present.
        """
        ...

    # ------------------------------------------------------------------
    # Defaults (override only when the tool deviates from the pattern)
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def ecosystem() -> str:
        """Return the ecosystem this SCM environment belongs to.

        Examples: `"git"`, `"hg"`.
        """
        ...

    @staticmethod
    def plugin_kind() -> PluginKind:
        """SCM environments always have kind `SCM`."""
        return PluginKind.SCM

    def clone_command(self, url: str, destination: Path) -> list[str]:
        """Return the CLI command for cloning a repository.

        Built from `tool_name()` and the provided URL/destination.
        This is used for displaying commands in dry-run / preview mode.

        Args:
            url: The repository URL.
            destination: Local filesystem path for the clone.

        Returns:
            The CLI command as a list of strings.
        """
        return [self.tool_name(), 'clone', url, str(destination)]
