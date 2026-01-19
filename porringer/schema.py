"""Schema"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from importlib.metadata import Distribution
from logging import Logger
from pathlib import Path

from packaging.version import Version
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field


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
    """

    action_type: SetupActionType
    description: str
    plugin: str | None = None
    package: str | None = None
    command: list[str] | None = None


@dataclass
class SetupActionResult:
    """Result of executing a single setup action.

    Args:
        action: The action that was executed.
        success: Whether the action succeeded.
        message: Optional message (error details on failure).
    """

    action: SetupAction
    success: bool
    message: str | None = None


class Prerequisite(BaseModel):
    """A prerequisite plugin that must be available."""

    plugin: str = Field(description='The plugin name that must be available')


class SetupManifest(BaseModel):
    """The setup manifest schema for .porringer files or pyproject.toml [tool.porringer]."""

    version: str = Field(default='1', description='Manifest schema version')
    prerequisites: list[Prerequisite] = Field(
        default_factory=list, description='Plugins that must be available before setup'
    )
    packages: dict[str, list[str]] = Field(
        default_factory=dict, description='Packages to install per plugin (plugin name -> package list)'
    )
    post_install: list[str] = Field(default_factory=list, description='Commands to run after package installation')


class SetupParameters(BaseModel):
    """Parameters for the setup command."""

    path: Path = Field(default=Path('.'), description='Path to manifest file or directory containing one')
    timeout: int = Field(default=300, description='Timeout in seconds for post-install commands')


@dataclass
class SetupResults:
    """Results of a setup operation.

    Args:
        actions: The list of actions (for preview) or action results (for execute).
        manifest_path: The path to the manifest that was used.
    """

    actions: list[SetupAction] = field(default_factory=list)
    results: list[SetupActionResult] = field(default_factory=list)
    manifest_path: Path | None = None


class UpdatePluginsParameters(BaseModel):
    """Parameters for updating plugins."""


@dataclass
class ListPluginResults:
    """Results of listing plugins.

    Args:
        name: The name of the plugin.
        version: The version of the plugin.
    """

    name: str
    version: Version
    installed: bool


@dataclass
class APIParameters:
    """Resolved configuration"""

    logger: Logger


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


# --- Update Schemas ---


class UpdateSource(Enum):
    """Source for checking updates"""

    GITHUB_RELEASES = auto()
    PYPI = auto()
    CUSTOM_URL = auto()


class CheckUpdateParameters(BaseModel):
    """Parameters for checking updates."""

    source: UpdateSource = Field(description='The update source to check')
    current_version: str = Field(description='The current version to compare against')
    repo: str | None = Field(default=None, description='GitHub repo in "owner/repo" format')
    package: str | None = Field(default=None, description='PyPI package name')
    url: str | None = Field(default=None, description='Custom URL for update manifest')
    github_token: str | None = Field(default=None, description='Optional GitHub token for rate limiting')
    include_prereleases: bool = Field(default=False, description='Include pre-release versions')


@dataclass
class UpdateInfo:
    """Information about available updates.

    Args:
        available: Whether an update is available.
        current_version: The current version.
        latest_version: The latest available version.
        download_url: URL to download the update.
        release_notes_url: URL to release notes.
        published_at: When the release was published.
    """

    available: bool
    current_version: Version
    latest_version: Version | None = None
    download_url: str | None = None
    release_notes_url: str | None = None
    published_at: datetime | None = None


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
