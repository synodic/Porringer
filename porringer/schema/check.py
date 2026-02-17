"""Check/update schemas."""

from dataclasses import dataclass, field

from packaging.version import Version
from pydantic import BaseModel, Field


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
