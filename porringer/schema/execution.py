"""Execution schemas."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from porringer.core.schema import Ecosystem, PackageRef, PluginKind
from porringer.schema.manifest import ManifestMetadata


class SkipReason(Enum):
    """Machine-readable reason an action was skipped.

    Use `SetupActionResult.message` for the human-readable detail.
    """

    ALREADY_INSTALLED = auto()
    NO_PROJECT_DIRECTORY = auto()
    UPDATE_AVAILABLE = auto()


@dataclass
class SetupAction:
    """A single action to perform during setup.

    The `kind` field is the primary discriminator:

    * `PluginKind.PACKAGE`, `PluginKind.TOOL`,
      `PluginKind.RUNTIME` — install/upgrade a single package.
    * `PluginKind.PROJECT` — sync a project lock-file / venv.
    * `PluginKind.SCM` — clone a repository.
    * `None` — run a post-sync shell command.

    When `plugin_target` is set the action is a *plugin-management*
    action: the `package` is added to the `plugin_target` parent
    tool via its native ``PluginManager`` (e.g.
    ``pdm self add cppython``).

    Args:
        description: Human-readable description of the action.
        kind: The plugin kind, or `None` for post-sync commands.
        ecosystem: The ecosystem identifier (e.g. `"python"`, `"node"`).
        installer: The plugin name (for PACKAGE/TOOL/RUNTIME/PROJECT/SCM).
        package: The package reference (for PACKAGE/TOOL/RUNTIME/SCM).
        plugin_target: The parent tool for plugin actions, or `None`.
        command: The command to run (for post-sync commands).
        cli_command: The actual CLI command (for display purposes).
        package_description: Optional per-package description from the manifest.
        include_prereleases: Per-package opt-in for pre-release update detection.
    """

    description: str
    kind: PluginKind | None = None
    ecosystem: Ecosystem | None = None
    installer: str | None = None
    package: PackageRef | None = None
    plugin_target: PackageRef | None = None
    command: list[str] | None = None
    cli_command: list[str] | None = None
    package_description: str | None = None
    include_prereleases: bool = False


@dataclass
class SetupActionResult:
    """Result of executing a single setup action.

    Args:
        action: The action that was executed.
        success: Whether the action succeeded.
        message: Optional human-readable detail (error on failure, description on skip).
        skipped: Whether the action was skipped.
        skip_reason: Machine-readable skip code (see `SkipReason`).
        installed_version: The currently installed version string, populated
            when the action is skipped due to presence detection.
        available_version: The latest upstream version string, populated
            when ``skip_reason`` is ``UPDATE_AVAILABLE``.
    """

    action: SetupAction
    success: bool
    message: str | None = None
    skipped: bool = False
    skip_reason: SkipReason | None = None
    installed_version: str | None = None
    available_version: str | None = None


class SyncStrategy(Enum):
    """Strategy controlling how the sync engine reconciles manifest state.

    MINIMAL: Default.  Install packages that aren't already present.
             Already-installed packages are left untouched.
    LATEST:  Upgrade every package to its latest allowed version.
             Falls back to install if a package isn't installed.
    EXACT:   Ensure each package satisfies the declared constraint.
             Upgrade if installed, install if not.
    """

    MINIMAL = auto()
    LATEST = auto()
    EXACT = auto()


class SetupParameters(BaseModel):
    """Parameters for the setup command."""

    paths: Path | Sequence[Path] | None = Field(
        default=None, description='Path(s) to manifest file(s) or directories. None uses all cached directories.'
    )
    project_directory: Path | Literal[False] | None = Field(
        default=None,
        description=(
            'Controls where project-sync and post-sync actions run. '
            'None (default) lets each project-environment plugin auto-discover '
            'its project root by walking ancestor directories from the manifest '
            'location looking for an ecosystem-specific marker file '
            '(e.g. pyproject.toml for Python, package.json for Node). '
            'A Path overrides the working directory for all plugins, '
            'disabling per-ecosystem auto-discovery. '
            'False skips project-sync actions entirely.'
        ),
    )
    timeout: int = Field(default=300, description='Timeout in seconds for post-sync commands')
    fail_fast: bool = Field(default=True, description='Stop on first error when processing multiple paths')
    dry_run: bool = Field(default=False, description='Preview actions without executing them')
    strategy: SyncStrategy = Field(default=SyncStrategy.MINIMAL, description='Sync strategy: minimal, latest, or exact')
    detect_updates: bool = Field(
        default=False,
        description=(
            'When True and dry_run is True, installed packages are checked '
            'for newer upstream versions via each plugin\u2019s native tooling. '
            'Adds network latency; the GUI sets this explicitly.'
        ),
    )
    prerelease_packages: set[str] | None = Field(
        default=None,
        description=(
            'Set of package names whose ``include_prereleases`` flag '
            'should be forced to ``True``, overriding the manifest '
            'default.  Names are compared case-insensitively against '
            '``action.package.name``.  ``None`` means no overrides.'
        ),
    )
    plugins: list[str] | None = Field(
        default=None,
        description=(
            'List of plugin names to include. None means all plugins.'
            ' Only actions handled by named plugins will be executed.'
        ),
    )


@dataclass
class SetupResults:
    """Results of a setup operation.

    Args:
        actions: The list of actions (for preview) or action results (for execute).
        manifest_path: The path to the manifest that was used.
        root_directory: The logical project root directory.
        metadata: Optional display metadata from the manifest.
    """

    actions: list[SetupAction] = field(default_factory=list)
    results: list[SetupActionResult] = field(default_factory=list)
    manifest_path: Path | None = None
    root_directory: Path | None = None
    metadata: ManifestMetadata | None = None


@dataclass
class BatchSetupResults:
    """Results of batch setup operations across multiple manifests.

    Args:
        manifest_results: Results for each manifest processed.
        failed_paths: Paths that failed to process (e.g., manifest not found).
    """

    manifest_results: list[SetupResults] = field(default_factory=list)
    failed_paths: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Returns True if all manifests were processed successfully."""
        if self.failed_paths:
            return False
        return all(all(r.success for r in m.results) for m in self.manifest_results)

    @property
    def total_actions(self) -> int:
        """Total number of actions across all manifests."""
        return sum(len(m.actions) for m in self.manifest_results)

    @property
    def total_succeeded(self) -> int:
        """Total number of successful action results (excludes skipped)."""
        return sum(1 for m in self.manifest_results for r in m.results if r.success and not r.skipped)

    @property
    def total_failed(self) -> int:
        """Total number of failed action results."""
        return sum(1 for m in self.manifest_results for r in m.results if not r.success)

    @property
    def total_skipped(self) -> int:
        """Total number of skipped action results."""
        return sum(1 for m in self.manifest_results for r in m.results if r.skipped)

    @property
    def skips(self) -> list[SetupActionResult]:
        """All skipped action results.

        Each result carries `skip_reason` (`SkipReason` enum),
        `message` (human-readable detail), and `action` (with
        `action_type`, `kind`, `ecosystem`, `installer`) for programmatic
        inspection.
        """
        return [r for m in self.manifest_results for r in m.results if r.skipped]
