"""Core helpers and types for project environment."""

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

import json
import logging
import tomllib
from abc import abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, override

from pydantic import Field

from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Ecosystem, ManifestContribution, PluginKind, PorringerModel

logger = logging.getLogger(__name__)

# Default mapping from ecosystem name to the file that marks a project root.
ECOSYSTEM_MARKERS: dict[Ecosystem, str] = {
    Ecosystem('python'): 'pyproject.toml',
    Ecosystem('node'): 'package.json',
}

# Default mapping from ecosystem name to the manifest contribution.
ECOSYSTEM_CONTRIBUTIONS: dict[Ecosystem, ManifestContribution] = {
    Ecosystem('python'): ManifestContribution(
        filename='pyproject.toml', config_path=('tool', 'porringer'), file_format='toml'
    ),
    Ecosystem('node'): ManifestContribution(filename='package.json', config_path=('porringer',), file_format='json'),
}


class ProjectSyncParameters(PorringerModel):
    """Parameters for a project-level sync operation."""

    directory: Path = Field(description='Working directory for the sync command (manifest location)')
    dry: bool = Field(default=False, description='If True, preview the sync without modifying the environment')
    runtime_context: RuntimeContext | None = Field(
        default=None,
        exclude=True,
        description='Resolved runtime paths for this execution run.',
    )


class ProjectCommandPlan(PorringerModel):
    """A project-sync command plan produced by a project plugin."""

    directory: Path = Field(description='Working directory for the sync command')
    argv: list[str] = Field(default_factory=list, description='Primary command and arguments to execute')
    steps: list[list[str]] = Field(default_factory=list, description='Ordered command steps to execute')


class ProjectEnvironment(ToolBasedPlugin, RuntimeConsumer, ManifestContributor):
    """Plugin definition for project-scoped dependency managers.

    Unlike `Environment`,
    which installs individual packages, a `ProjectEnvironment` runs the
    tool's native *sync* / *install* command inside the project directory.
    Venv creation, lock-file handling, and dependency resolution are left
    entirely to the wrapped tool.

    Subclasses **must** override `tool_name()`.  Everything else has
    sensible defaults that can be overridden when the tool's CLI differs
    from the common pattern (e.g. Poetry's `poetry env use` step).

    Plugin instances are **stateless** with respect to runtime
    configuration.  A :class:`RuntimeContext` is passed explicitly to
    methods that need to know which interpreter to target.
    """

    _sync_verb: str = 'install'
    """The sub-command the tool uses for project synchronisation.

    Defaults to `"install"` (used by PDM and Poetry).
    Override to `"sync"` for tools like uv.
    """

    _supports_dry_run: bool = True
    """Whether the wrapped tool supports a native ``--dry-run`` flag.

    When `True` (the default), `sync()` appends `--dry-run` for dry
    runs.  Override to `False` for tools that lack it (e.g. pnpm,
    Yarn Berry); dry runs then log the command without
    executing it.
    """

    _project_evidence_files: ClassVar[tuple[str, ...]] = ()
    """Files that indicate this specific project manager owns the project."""

    _pyproject_tool_tables: ClassVar[tuple[tuple[str, ...], ...]] = ()
    """TOML table paths in pyproject.toml that indicate project ownership."""

    _package_manager_names: ClassVar[tuple[str, ...]] = ()
    """package.json packageManager prefixes that indicate project ownership."""

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
    def ecosystem() -> Ecosystem:
        """Return the ecosystem this project environment belongs to.

        Examples: `"python"`, `"node"`.
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

        Examples: `"python"`, `"node"`.
        """
        ...

    @classmethod
    def manifest_contribution(cls) -> ManifestContribution | None:
        """Return the manifest contribution for this ecosystem.

        The default implementation looks up ``ecosystem()`` in
        ``ECOSYSTEM_CONTRIBUTIONS``.  Override this method for
        plugins that use a non-standard hosted config layout.

        Returns:
            A ``ManifestContribution`` describing the hosted config, or
            ``None`` if this ecosystem does not embed porringer config.
        """
        return ECOSYSTEM_CONTRIBUTIONS.get(cls.ecosystem())

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

    def sync_command(self, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Return the CLI command for syncing the project.

        Built from `tool_name()` and `_sync_verb`, with
        `--python <path>` appended when *runtime_context* supplies
        a resolved interpreter for this plugin's consumed runtime kind.

        This is used for displaying commands in dry-run / preview mode.

        Args:
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use defaults.
        """
        cmd = [self.tool_name(), self._sync_verb]
        if runtime_context is not None:
            exe = runtime_context.get(self.consumed_runtime_kind())
            if exe is not None:
                cmd.extend(['--python', str(exe)])
        return cmd

    @classmethod
    def project_relevance(cls, search_from: Path) -> bool:
        """Return whether this plugin should run for the given directory.

        The default implementation looks for the plugin's marker file in
        the current directory or an ancestor, mirroring the existing
        project-root discovery behavior.
        """
        return cls.resolve_project_root(search_from) is not None

    @classmethod
    def project_evidence(cls, search_from: Path) -> bool:
        """Return whether project files specifically identify this plugin.

        Marker files such as ``pyproject.toml`` and ``package.json`` establish
        project relevance for an ecosystem. Evidence is narrower: lock files,
        tool-specific config tables, or package-manager declarations identify
        which project manager should own sync for that project.
        """
        directory = cls.resolve_project_root(search_from)
        if directory is None:
            return False

        if any((directory / filename).exists() for filename in cls._project_evidence_files):
            return True

        pyproject = directory / 'pyproject.toml'
        if cls._pyproject_tool_tables and pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding='utf-8'))
            except OSError, tomllib.TOMLDecodeError:
                data = {}
            if any(_has_nested_key(data, table_path) for table_path in cls._pyproject_tool_tables):
                return True

        package_json = directory / 'package.json'
        if cls._package_manager_names and package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding='utf-8'))
            except OSError, json.JSONDecodeError:
                data = {}
            package_manager = data.get('packageManager') if isinstance(data, dict) else None
            if isinstance(package_manager, str) and package_manager.startswith(cls._package_manager_names):
                return True

        return False

    @classmethod
    def command_plan(cls, search_from: Path, *, runtime_context: RuntimeContext | None = None) -> ProjectCommandPlan:
        """Build a sync command plan for the provided directory."""
        directory = cls.resolve_project_root(search_from) or search_from
        cmd = [cls.tool_name(), cls._sync_verb]
        if runtime_context is not None:
            exe = runtime_context.get(cls.consumed_runtime_kind())
            if exe is not None:
                cmd.extend(['--python', str(exe)])
        return ProjectCommandPlan(directory=directory, argv=cmd, steps=[cmd])

    async def sync(self, params: ProjectSyncParameters) -> bool:
        """Run the tool's native sync/install in *params.directory*.

        The default implementation builds the command from
        `sync_command()` (which already includes `--python` when a
        runtime override is active) and appends `--dry-run` for dry
        runs when the tool supports it (`_supports_dry_run`).

        When the tool lacks a native `--dry-run` (`_supports_dry_run`
        is `False`), dry runs log the command without executing it.

        Override this method when the tool requires a different CLI shape
        (e.g. Poetry needs `poetry env use` before `poetry install`).

        Args:
            params: Sync parameters (directory, dry-run flag, runtime context).

        Returns:
            `True` on success, `False` on failure.
        """
        args = list(self.sync_command(runtime_context=params.runtime_context))
        if params.dry:
            if not self._supports_dry_run:
                logger.info('Dry run: %s', ' '.join(args))
                return True
            args.append('--dry-run')
        return await self._run_sync(args, params.directory)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_sync(self, args: list[str], directory: Path) -> bool:
        """Run a sync subprocess and return success.

        Shared helper that handles logging and error handling so each
        plugin's `sync()` implementation stays minimal.

        Args:
            args: Full command-line arguments.
            directory: Working directory.

        Returns:
            `True` if the process exited cleanly.
        """
        return await self._run_bool_command(args, cwd=directory, label='sync')


class NodeProjectEnvironment(ProjectEnvironment):
    """Base for Node-ecosystem project environments (npm, pnpm, yarn).

    Centralises the `node` ecosystem and consumed-runtime kind so that
    each concrete plugin only declares its `tool_name()` and any CLI
    deviations (e.g. ``_supports_dry_run``).
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Node project environments belong to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Node project environments consume a Node runtime."""
        return 'node'


def _has_nested_key(data: object, path: Sequence[str]) -> bool:
    """Return whether *data* contains the nested mapping path."""
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return True
