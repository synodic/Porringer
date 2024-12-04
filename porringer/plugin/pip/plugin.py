"""Plugin implementation"""

from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName


class PipEnvironment(Environment):
    """_summary_"""

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
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        """Upgrades the given list of packages

        Args:
            params: The upgrade parameters

        Returns:
            A list of packages that were upgraded. Each item could be None if there was a failure
        """
        return []

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        return []
