"""Backend schema"""

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, DirectoryPath, Field


class GlobalConfiguration(BaseModel):
    """Global configuration that Porringer manages"""

    config_directory: Path = Field(
        default=Path(user_config_dir('porringer', 'synodic')),
        description='The configuration directory',
    )

    data_directory: Path = Field(
        default=Path(user_data_dir('porringer', 'synodic')),
        description='The data directory',
    )


class Configuration(BaseModel):
    """Resolved configuration"""

    cache_directory: DirectoryPath
    config_directory: DirectoryPath
    data_directory: DirectoryPath


# --- Plugin Command Parameters ---


class PluginInstallParameters(BaseModel):
    """Parameters for installing a plugin."""

    name: str = Field(description='The PyPI package name of the plugin to install')
    dry: bool = Field(default=False, description='If True, show what would be done without executing')


class PluginUninstallParameters(BaseModel):
    """Parameters for uninstalling plugins."""

    names: list[str] = Field(description='The PyPI package names of the plugins to remove')
    dry: bool = Field(default=False, description='If True, show what would be done without executing')


class PluginUpdateParameters(BaseModel):
    """Parameters for updating plugins."""

    names: list[str] = Field(description='The PyPI package names of the plugins to update')
    dry: bool = Field(default=False, description='If True, show what would be done without executing')


@dataclass
class PluginOperationResult:
    """Result of a plugin operation (install/uninstall/update).

    Args:
        plugin_name: The name of the plugin that was operated on.
        success: Whether the operation succeeded.
        message: Human-readable message describing the result.
    """

    plugin_name: str
    success: bool
    message: str
