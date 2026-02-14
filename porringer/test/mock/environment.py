"""Mock environment data"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class MockEnvironment(Environment):
    """Mocked environment plugin"""

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package."""
        return ['mock', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package."""
        return ['mock', 'upgrade', str(package)]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name

        Args:
            params: The package parameters

        Returns:
            The package, or None if it doesn't exist
        """

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            package: The package reference to search for

        Returns:
            The package, or None if it doesn't exist
        """

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages

        Args:
            params: The uninstall parameters

        Returns:
            A list of packages that were uninstalled. Each item could be None if there was a failure
        """
        return []

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given package.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        return Package(name=params.package.name, version=None)

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages in the given environment

        Args:
            project_path: Unused in mock.

        Returns:
            A list of packages
        """
        return []
