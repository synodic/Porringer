"""Plugin utilities for project-scoped environments.

A `ProjectEnvironment` plugin wraps a project dependency manager
(PDM, Poetry, uv) and delegates venv creation, dependency resolution,
and lock-file synchronisation entirely to the underlying tool.

The sync engine invokes `ProjectEnvironment.sync()` after all
per-package actions have completed so that the tool itself is already
installed (e.g. via pipx).  When the manifest file lives in a
subdirectory of the project root, each plugin auto-discovers the
correct project root by walking ancestor directories looking for its
ecosystem's marker file (e.g. `package.json` for Node,
`pyproject.toml` for Python).
"""

import logging
import subprocess
from abc import abstractmethod
from pathlib import Path

from pydantic import Field

from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import PluginKind, PluginParameters, PorringerModel

logger = logging.getLogger(__name__)

# Default mapping from ecosystem name to the file that marks a project root.
ECOSYSTEM_MARKERS: dict[str, str] = {
    'python': 'pyproject.toml',
    'node': 'package.json',
    'deno': 'deno.json',
}


class ProjectSyncParameters(PorringerModel):
    """Parameters for a project-level sync operation."""

    directory: Path = Field(description='Working directory for the sync command (manifest location)')
    dry: bool = Field(default=False, description='If True, preview the sync without modifying the environment')


class ProjectEnvironment(ToolBasedPlugin, RuntimeConsumer):
    """Plugin definition for project-scoped dependency managers.

    Unlike `Environment`,
    which installs individual packages, a `ProjectEnvironment` runs the
    tool's native *sync* / *install* command inside the project directory.
    Venv creation, lock-file handling, and dependency resolution are left
    entirely to the wrapped tool.

    Subclasses **must** override `tool_name()`.  Everything else has
    sensible defaults that can be overridden when the tool's CLI differs
    from the common pattern (e.g. Poetry's `poetry env use` step).
    """

    _sync_verb: str = 'install'
    """The sub-command the tool uses for project synchronisation.

    Defaults to `"install"` (used by PDM and Poetry).
    Override to `"sync"` for tools like uv.
    """

    runtime_executable: Path | None
    """Override the language runtime interpreter for this project.

    When set by a `RuntimeProvider`
    during phased execution, the sync command is invoked with a flag
    that selects this interpreter (e.g. `--python <path>`).
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the project environment plugin.

        Args:
            parameters: Plugin parameters including distribution info
        """
        super().__init__(parameters)
        self.runtime_executable = None

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def tool_name(cls) -> str:
        """Return the CLI executable name this plugin wraps.

        Used by `is_available()` to verify the tool is on PATH and
        by `sync_command()` to build the default command.
        """
        ...

    # ------------------------------------------------------------------
    # Defaults (override only when the tool deviates from the pattern)
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def ecosystem() -> str:
        """Return the ecosystem this project environment belongs to.

        Examples: `"python"`, `"node"`, `"deno"`.
        """
        ...

    @staticmethod
    def plugin_kind() -> PluginKind:
        """Project environments always have kind `PROJECT`."""
        return PluginKind.PROJECT

    @classmethod
    @abstractmethod
    def consumed_runtime_kind(cls) -> str:
        """Return the kind of runtime this project environment consumes.

        Examples: `"python"`, `"node"`, `"deno"`.
        """
        ...

    @classmethod
    def project_marker(cls) -> str | None:
        """Return the filename that marks this ecosystem's project root.

        The sync engine uses this marker to auto-discover the project
        root when the manifest file lives in a subdirectory.  It walks
        ancestor directories starting from the manifest's location
        and returns the first directory containing this file.

        The default implementation looks up `ecosystem()` in a
        built-in mapping:

        ==========  ================
        Ecosystem   Marker
        ==========  ================
        `python`  `pyproject.toml`
        `node`    `package.json`
        `deno`    `deno.json`
        ==========  ================

        Override this method when a plugin uses a non-standard marker
        or when multiple markers should be checked.

        Returns:
            Filename to search for, or `None` to disable
            auto-discovery (always use the manifest directory).
        """
        return ECOSYSTEM_MARKERS.get(cls.ecosystem())

    @classmethod
    def resolve_project_root(
        cls,
        search_from: Path,
        *,
        boundary: Path | None = None,
    ) -> Path | None:
        """Walk ancestor directories looking for `project_marker()`.

        Starting from *search_from* (inclusive) and moving towards the
        filesystem root, return the first directory that contains the
        marker file returned by `project_marker()`.

        Args:
            search_from: Directory to start the search from
                (typically the manifest's parent directory).
            boundary: Optional upper-bound directory.  The search
                will not ascend above this path.  When `None`, the
                search continues to the filesystem root.

        Returns:
            The discovered project root, or `None` if the marker
            was not found (or `project_marker()` returns `None`).
        """
        marker = cls.project_marker()
        if marker is None:
            return None

        current = search_from.resolve()
        boundary_resolved = boundary.resolve() if boundary is not None else None

        while True:
            if (current / marker).exists():
                return current

            # Stop if we've reached the boundary
            if boundary_resolved is not None and current == boundary_resolved:
                break

            parent = current.parent
            # Stop at the filesystem root
            if parent == current:
                break
            current = parent

        return None

    def sync_command(self) -> list[str]:
        """Return the CLI command for syncing the project.

        Built from `tool_name()` and `_sync_verb`, with
        `--python <path>` appended when a runtime override is active.

        This is used for displaying commands in dry-run / preview mode
        and should reflect instance state.
        """
        cmd = [self.tool_name(), self._sync_verb]
        if self.runtime_executable is not None:
            cmd.extend(['--python', str(self.runtime_executable)])
        return cmd

    def sync(self, params: ProjectSyncParameters) -> bool:
        """Run the tool's native sync/install in *params.directory*.

        The default implementation builds the command from
        `sync_command()` (which already includes `--python` when a
        runtime override is active) and appends `--dry-run` for dry
        runs.

        Override this method when the tool requires a different CLI shape
        (e.g. Poetry needs `poetry env use` before `poetry install`).

        Args:
            params: Sync parameters (directory, dry-run flag).

        Returns:
            `True` on success, `False` on failure.
        """
        args = list(self.sync_command())
        if params.dry:
            args.append('--dry-run')
        return self._run_sync(args, params.directory)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_sync(self, args: list[str], directory: Path) -> bool:
        """Run a sync subprocess and return success.

        Shared helper that handles logging and error handling so each
        plugin's `sync()` implementation stays minimal.

        Args:
            args: Full command-line arguments.
            directory: Working directory.

        Returns:
            `True` if the process exited cleanly.
        """
        tool = self.tool_name()
        sync_logger = logging.getLogger(f'porringer.{tool}.sync')
        try:
            result = subprocess.run(args, cwd=directory, capture_output=True, text=True, check=False)
            sync_logger.info(result.stdout)
            if result.returncode != 0:
                sync_logger.error(result.stderr)
                return False
        except FileNotFoundError:
            sync_logger.error('%s not found on PATH', tool)
            return False
        except subprocess.SubprocessError as e:
            sync_logger.error('Failed to sync project: %s', e)
            return False
        return True
