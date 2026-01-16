"""Schema"""

from dataclasses import dataclass, field
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
