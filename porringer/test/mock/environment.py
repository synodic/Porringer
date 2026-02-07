"""Mock environment data"""

from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName


class MockEnvironment(Environment):
    """Mocked environment plugin"""

    @staticmethod
    @override
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command to install a package."""
        return ['mock', 'install', str(package)]

    @staticmethod
    @override
    def upgrade_command(package: PackageName) -> list[str]:
        """Returns the CLI command to upgrade a package."""
        return ['mock', 'upgrade', str(package)]

    @override
    def install(self, params: InstallParameters) -> Package | None:
        """Installs the given package identified by its name

        Args:
            params: The installation parameters

        Returns:
            The package, or None if it doesn't exist
        """

    @override
    def search(self, name: PackageName) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            name: The package name to search for

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
    def upgrade(self, params: UpgradeParameters) -> Package | None:
        """Upgrades the given package.

        Args:
            params: The upgrade parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        return Package(name=params.name, version=None)

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        return []
