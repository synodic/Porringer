"""Schema"""

import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from importlib.metadata import Distribution
from pathlib import Path

from packaging.version import Version
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field, HttpUrl, field_validator

# --- Directory Cache Schemas ---


class ManifestDirectory(BaseModel):
    """A directory containing manifest files."""

    path: Path = Field(description='Absolute path to directory')
    name: str | None = Field(default=None, description='Optional display name/alias')


class DirectoryCache(BaseModel):
    """Persisted cache of manifest directories."""

    version: str = Field(default='1', description='Cache schema version')
    directories: list[ManifestDirectory] = Field(default_factory=list, description='Registered directories')


# --- Command Parameter Schemas ---


class UpdatePorringerParameters(BaseModel):
    """Parameters for updating the Porringer application."""


class CheckPorringerParameters(BaseModel):
    """Parameters for checking the Porringer application status."""


class ListPluginsParameters(BaseModel):
    """Parameters for listing available plugins."""

    pattern: str = Field(default='*', description='The pattern to match against')


# --- Setup Schemas ---


class SetupActionType(Enum):
    """The type of action to perform during setup"""

    CHECK_PLUGIN = auto()
    INSTALL_PACKAGE = auto()
    RUN_COMMAND = auto()


@dataclass
class SetupAction:
    """A single action to perform during setup.

    Args:
        action_type: The type of action.
        plugin: The plugin name (for CHECK_PLUGIN and INSTALL_PACKAGE).
        package: The package name (for INSTALL_PACKAGE).
        command: The command to run (for RUN_COMMAND).
        description: Human-readable description of the action.
        cli_command: The actual CLI command (for display purposes).
        package_description: Optional per-package description from the manifest.
    """

    action_type: SetupActionType
    description: str
    plugin: str | None = None
    package: str | None = None
    command: list[str] | None = None
    cli_command: list[str] | None = None
    package_description: str | None = None


@dataclass
class SetupActionResult:
    """Result of executing a single setup action.

    Args:
        action: The action that was executed.
        success: Whether the action succeeded.
        message: Optional message (error details on failure).
        skipped: Whether the action was skipped (e.g., plugin check found plugin).
        skip_reason: Human-readable reason for skipping (e.g., 'already installed').
    """

    action: SetupAction
    success: bool
    message: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class SubActionProgress:
    """Fine-grained progress update from within a plugin operation.

    Plugins emit these to report phases and percentages during long-running
    operations (e.g., downloading a wheel, verifying checksums).

    Args:
        action: The parent setup action this progress belongs to.
        phase: Current phase (e.g. ``"downloading"``, ``"installing"``, ``"verifying"``).
        progress: 0.0–1.0 completion fraction, or ``None`` if indeterminate.
        message: Human-readable status line (e.g. ``"Downloading ruff-0.8.0.whl (2.1 MB)"``).
    """

    action: SetupAction
    phase: str
    progress: float | None = None
    message: str | None = None


class ProgressEventKind(Enum):
    """The kind of progress event emitted during setup execution."""

    ACTION_STARTED = auto()
    ACTION_COMPLETED = auto()
    SUB_ACTION_PROGRESS = auto()


@dataclass
class ProgressEvent:
    """A single progress event from the setup execution stream.

    Consumers iterate over ``AsyncIterator[ProgressEvent]`` to observe
    action lifecycle and sub-action detail updates.

    Args:
        kind: What this event represents.
        action: The setup action this event relates to.
        result: Action result (set only for ``ACTION_COMPLETED``).
        sub_action: Sub-action detail (set only for ``SUB_ACTION_PROGRESS``).
    """

    kind: ProgressEventKind
    action: SetupAction
    result: SetupActionResult | None = None
    sub_action: SubActionProgress | None = None


@dataclass
class CancellationToken:
    """Token for cooperative cancellation of async operations.

    Used by GUI applications to request cancellation of long-running
    async operations like batch installs.

    Example:
        token = CancellationToken()
        task = asyncio.create_task(long_operation(token))
        # Later...
        token.cancel()
    """

    _cancelled: bool = field(default=False, init=False)
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def cancel(self) -> None:
        """Request cancellation of the operation."""
        self._cancelled = True
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled

    async def wait_cancelled(self) -> None:
        """Wait until cancellation is requested."""
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        """Raise asyncio.CancelledError if cancellation was requested."""
        if self._cancelled:
            raise asyncio.CancelledError('Operation cancelled by token')


class Prerequisite(BaseModel):
    """A prerequisite plugin that must be available."""

    plugin: str = Field(description='The plugin name that must be available')
    platforms: list[str] = Field(
        default_factory=list,
        description='List of platforms where this prerequisite applies (e.g., ["win32"]). Empty means all platforms.',
    )

    def is_applicable(self) -> bool:
        """Check if this prerequisite applies to the current platform.

        Returns:
            True if the prerequisite applies to the current platform
        """
        if not self.platforms:
            return True
        return sys.platform in self.platforms


class PackageSpec(BaseModel):
    """A package entry with optional display metadata.

    Supports both string shorthand (just a package name) and object form
    with additional metadata for GUI consumers.
    """

    name: str = Field(description='The package name')
    description: str | None = Field(default=None, description='Human-readable description of this package')


class SetupManifest(BaseModel):
    """The setup manifest schema for .porringer files or pyproject.toml [tool.porringer]."""

    version: str = Field(default='1', description='Manifest schema version')
    name: str | None = Field(default=None, description='Human-readable project/environment name')
    description: str | None = Field(default=None, description='Short description shown in the install preview header')
    author: str | None = Field(default=None, description='Author or organization name')
    url: HttpUrl | None = Field(default=None, description='Project URL for reference')
    prerequisites: list[Prerequisite] = Field(
        default_factory=list, description='Plugins that must be available before setup'
    )
    packages: dict[str, list[PackageSpec]] = Field(
        default_factory=dict, description='Packages to install per plugin (plugin name -> package list)'
    )
    post_install: list[str] = Field(default_factory=list, description='Commands to run after package installation')

    @field_validator('packages', mode='before')
    @classmethod
    def _normalize_packages(cls, value: dict[str, list[str | dict]]) -> dict[str, list[dict]]:
        """Normalize package entries: coerce plain strings into PackageSpec dicts."""
        normalized: dict[str, list[dict]] = {}
        for plugin, entries in value.items():
            normalized_entries: list[dict] = []
            for entry in entries:
                if isinstance(entry, str):
                    normalized_entries.append({'name': entry})
                else:
                    normalized_entries.append(entry)
            normalized[plugin] = normalized_entries
        return normalized


class SetupParameters(BaseModel):
    """Parameters for the setup command."""

    paths: Path | Sequence[Path] | None = Field(
        default=None, description='Path(s) to manifest file(s) or directories. None uses all cached directories.'
    )
    timeout: int = Field(default=300, description='Timeout in seconds for post-install commands')
    fail_fast: bool = Field(default=True, description='Stop on first error when processing multiple paths')
    dry_run: bool = Field(default=False, description='Preview actions without executing them')


@dataclass
class ManifestMetadata:
    """Display metadata from a setup manifest.

    Carries optional human-readable information for GUI consumers
    (e.g. install preview screens).

    Args:
        name: Human-readable project/environment name.
        description: Short description shown in the install preview header.
        author: Author or organization name.
        url: Project URL for reference.
    """

    name: str | None = None
    description: str | None = None
    author: str | None = None
    url: str | None = None


@dataclass
class SetupResults:
    """Results of a setup operation.

    Args:
        actions: The list of actions (for preview) or action results (for execute).
        manifest_path: The path to the manifest that was used.
        metadata: Optional display metadata from the manifest.
    """

    actions: list[SetupAction] = field(default_factory=list)
    results: list[SetupActionResult] = field(default_factory=list)
    manifest_path: Path | None = None
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
        """Total number of successful action results."""
        return sum(sum(1 for r in m.results if r.success) for m in self.manifest_results)

    @property
    def total_failed(self) -> int:
        """Total number of failed action results."""
        return sum(sum(1 for r in m.results if not r.success) for m in self.manifest_results)


class UpdatePluginsParameters(BaseModel):
    """Parameters for updating plugins."""


@dataclass
class ListPluginResults:
    """Results of listing plugins.

    Args:
        name: The name of the plugin.
        version: The version of the plugin.
        installed: Whether the underlying package manager is available on the system.
    """

    name: str
    version: Version
    installed: bool


@dataclass
class APIParameters:
    """Resolved configuration"""

    pass


@dataclass
class PluginInformation[Plugin]:
    """Gathered information about available plugins"""

    type: type[Plugin]
    distribution: Distribution


class LocalConfiguration(BaseModel):
    """Configuration provided by the application running Porringer"""

    cache_directory: Path = Field(
        default=Path(user_cache_dir('porringer', 'synodic')), description='The application cache path '
    )


# --- Check Schemas (Plugin-delegated) ---


@dataclass
class PackageUpdateInfo:
    """Update information for a single package.

    Args:
        name: Package name.
        current_version: Currently installed version.
        latest_version: Latest available version.
        update_available: Whether an update is available.
    """

    name: str
    current_version: Version | None
    latest_version: Version | None
    update_available: bool


@dataclass
class CheckResult:
    """Result of checking updates for a plugin.

    Args:
        plugin: The plugin name.
        packages: List of package update info.
        error: Optional error message if check failed.
    """

    plugin: str
    packages: list[PackageUpdateInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        """Returns True if the check completed without error."""
        return self.error is None

    @property
    def updates_available(self) -> int:
        """Returns the count of packages with updates available."""
        return sum(1 for p in self.packages if p.update_available)


class CheckParameters(BaseModel):
    """Parameters for checking updates via plugins."""

    plugins: list[str] | None = Field(
        default=None, description='List of plugin names to check. None means all plugins.'
    )
    include_prereleases: bool = Field(default=False, description='Include pre-release versions')


class HashAlgorithm(Enum):
    """Supported hash algorithms for verification"""

    SHA256 = 'sha256'
    SHA512 = 'sha512'


class DownloadParameters(BaseModel):
    """Parameters for downloading files."""

    url: str = Field(description='URL to download')
    destination: Path = Field(description='Destination file path')
    expected_hash: str | None = Field(
        default=None, description='Expected hash in "algorithm:hexdigest" format (e.g., "sha256:abc123...")'
    )
    expected_size: int | None = Field(default=None, description='Expected file size in bytes')
    timeout: int = Field(default=300, description='Download timeout in seconds')
    chunk_size: int = Field(default=8192, description='Download chunk size in bytes')


# Type alias for progress callback: (downloaded_bytes, total_bytes) -> None
ProgressCallback = Callable[[int, int | None], None]


@dataclass
class DownloadResult:
    """Result of a download operation.

    Args:
        success: Whether the download succeeded.
        path: Path to the downloaded file.
        verified: Whether the hash was verified.
        size: Size of the downloaded file in bytes.
        message: Optional message (error details on failure).
    """

    success: bool
    path: Path | None = None
    verified: bool = False
    size: int = 0
    message: str | None = None
