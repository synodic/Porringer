"""Plugin utilities for package environments"""

from abc import abstractmethod
from typing import override

from pydantic import BaseModel, Field

from porringer.core.schema import (
    Information,
    Package,
    PackageName,
    Plugin,
    SupportedFeatures,
)


class InstallParameters(BaseModel):
    """The install parameters for an environment plugin"""

    name: PackageName = Field(description='The package to install')
    dry: bool = Field(
        default=False, description='If True, rehearses an installation without modifying what is actually installed'
    )


class UninstallParameters(BaseModel):
    """The uninstall parameters for an environment plugin"""

    names: list[PackageName] = Field(
        description='The list of packages to uninstall. If empty, all packages are uninstalled'
    )
    dry: bool = Field(
        default=False, description='If True, rehearses an uninstall without modifying what is actually installed'
    )


class UpgradeParameters(BaseModel):
    """The upgrade parameters for an environment plugin"""

    names: list[PackageName] = Field(description='The list of packages to upgrade. If empty, all packages are upgraded')
    dry: bool = Field(
        default=False, description='If True, rehearses an upgrade without modifying what is actually installed'
    )


class Environment(Plugin):
    """Plugin definition for package environments"""

    @staticmethod
    @override
    def features() -> SupportedFeatures:
        """Broadcasts the shared features of the plugin to Porringer

        Returns:
            The supported features
        """
        return SupportedFeatures()

    @staticmethod
    @override
    def information() -> Information:
        """Retrieves plugin information that complements the packaged project metadata

        Returns:
            The plugin's information
        """
        return Information()

    @abstractmethod
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, name: PackageName) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            name: The package name to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @abstractmethod
    def install(self, params: InstallParameters) -> Package | None:
        """Installs the given package identified by its name

        Args:
            params: The installation parameters

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @abstractmethod
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages

        Args:
            params: The uninstall parameters

        Returns:
            A list of packages that were uninstalled. Each item could be None if there was a failure
        """
        raise NotImplementedError

    @abstractmethod
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        """Upgrades the given list of packages

        Args:
            params: The upgrade parameters

        Returns:
            A list of packages that were upgraded. Each item could be None if there was a failure
        """
        raise NotImplementedError
