"""Plugin utilities for project-scoped environments.

A :class:`ProjectEnvironment` plugin wraps a project dependency manager
(PDM, Poetry, uv) and delegates venv creation, dependency resolution,
and lock-file synchronisation entirely to the underlying tool.

The sync engine invokes :meth:`ProjectEnvironment.sync` in the
manifest's directory after all per-package actions have completed so
that the tool itself is already installed (e.g. via pipx).
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


class ProjectSyncParameters(PorringerModel):
    """Parameters for a project-level sync operation."""

    directory: Path = Field(description='Working directory for the sync command (manifest location)')
    dry: bool = Field(default=False, description='If True, preview the sync without modifying the environment')


class ProjectEnvironment(ToolBasedPlugin, RuntimeConsumer):
    """Plugin definition for project-scoped dependency managers.

    Unlike :class:`~porringer.core.plugin_schema.environment.Environment`,
    which installs individual packages, a ``ProjectEnvironment`` runs the
    tool's native *sync* / *install* command inside the project directory.
    Venv creation, lock-file handling, and dependency resolution are left
    entirely to the wrapped tool.

    Subclasses **must** override :meth:`tool_name`.  Everything else has
    sensible defaults that can be overridden when the tool's CLI differs
    from the common pattern (e.g. Poetry's ``poetry env use`` step).
    """

    _sync_verb: str = 'install'
    """The sub-command the tool uses for project synchronisation.

    Defaults to ``"install"`` (used by PDM and Poetry).
    Override to ``"sync"`` for tools like uv.
    """

    runtime_executable: Path | None
    """Override the language runtime interpreter for this project.

    When set by a :class:`~porringer.core.plugin_schema.runtime.RuntimeProvider`
    during phased execution, the sync command is invoked with a flag
    that selects this interpreter (e.g. ``--python <path>``).
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

        Used by :meth:`is_available` to verify the tool is on PATH and
        by :meth:`sync_command` to build the default command.
        """
        ...

    # ------------------------------------------------------------------
    # Defaults (override only when the tool deviates from the pattern)
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def ecosystem() -> str:
        """Return the ecosystem this project environment belongs to.

        Examples: ``"python"``, ``"node"``, ``"deno"``.
        """
        ...

    @staticmethod
    def plugin_kind() -> PluginKind:
        """Project environments always have kind ``PROJECT``."""
        return PluginKind.PROJECT

    @classmethod
    @abstractmethod
    def consumed_runtime_kind(cls) -> str:
        """Return the kind of runtime this project environment consumes.

        Examples: ``"python"``, ``"node"``, ``"deno"``.
        """
        ...

    def sync_command(self) -> list[str]:
        """Return the CLI command for syncing the project.

        Built from :meth:`tool_name` and :attr:`_sync_verb`, with
        ``--python <path>`` appended when a runtime override is active.

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
        :meth:`sync_command` (which already includes ``--python`` when a
        runtime override is active) and appends ``--dry-run`` for dry
        runs.

        Override this method when the tool requires a different CLI shape
        (e.g. Poetry needs ``poetry env use`` before ``poetry install``).

        Args:
            params: Sync parameters (directory, dry-run flag).

        Returns:
            ``True`` on success, ``False`` on failure.
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
        plugin's :meth:`sync` implementation stays minimal.

        Args:
            args: Full command-line arguments.
            directory: Working directory.

        Returns:
            ``True`` if the process exited cleanly.
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
