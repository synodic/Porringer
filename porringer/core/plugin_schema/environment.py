"""Plugin utilities for package environments"""

import asyncio
from abc import abstractmethod
from collections.abc import Callable
from typing import override

from pydantic import BaseModel, Field

from porringer.core.schema import (
    Information,
    Package,
    PackageRef,
    Plugin,
    PorringerModel,
    SupportedFeatures,
)
from porringer.schema import SubActionProgress


class ProviderCapability(BaseModel):
    """Describes what a plugin can provide to other plugins.

    Provider plugins offer runtime environments or capabilities that other plugins
    depend on. For example, the pim plugin provides Python runtimes that pip/pipx need.
    """

    capability: str = Field(description='The capability identifier (e.g., "python-runtime")')
    description: str = Field(default='', description='Human-readable description of the capability')


class ProviderRequirement(BaseModel):
    """Describes a provider requirement for a plugin.

    Plugins can declare that they require a provider capability. When a provider
    is available, it enables additional functionality. For example, pip requires
    a Python runtime, which can be provided by the pim plugin.

    NOTE: Currently defaults to using the latest available instance from the provider.
    Future versions may support configuration for selecting specific instances.
    """

    capability: str = Field(description='The required capability identifier (e.g., "python-runtime")')
    required: bool = Field(
        default=False,
        description='Whether this provider is required (True) or optional (False). '
        'Most providers should be optional to allow fallback to system-installed instances.',
    )
    provider_plugin: str = Field(
        default='',
        description='Preferred plugin that provides this capability. Empty means any provider.',
    )


class PackageParameters(PorringerModel):
    """Parameters for a package install or upgrade operation."""

    package: PackageRef = Field(description='The target package')
    dry: bool = Field(
        default=False, description='If True, rehearses the operation without modifying what is actually installed'
    )
    progress_callback: Callable[[SubActionProgress], None] | None = Field(
        default=None,
        exclude=True,
        description='Optional callback for reporting sub-action progress (download %, install phase, etc.)',
    )


class UninstallParameters(PorringerModel):
    """The uninstall parameters for an environment plugin"""

    packages: list[PackageRef] = Field(
        description='The list of packages to uninstall. If empty, all packages are uninstalled'
    )
    dry: bool = Field(
        default=False, description='If True, rehearses an uninstall without modifying what is actually installed'
    )


class CheckUpdatesParameters(PorringerModel):
    """Parameters for checking updates via a plugin."""

    packages: list[PackageRef] = Field(
        default_factory=list, description='Packages to check for updates. Empty means check all installed packages.'
    )
    include_prereleases: bool = Field(default=False, description='Include pre-release versions')


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

    @staticmethod
    def provides() -> list[ProviderCapability]:
        """Declares capabilities that this plugin provides to other plugins.

        Provider plugins can offer runtime environments or other capabilities
        that consumer plugins depend on. Override this method to declare
        what your plugin provides.

        Example: The pim plugin provides "python-runtime" capability.

        Returns:
            A list of provider capabilities
        """
        return []

    @staticmethod
    def requires_providers() -> list[ProviderRequirement]:
        """Declares provider requirements for this plugin.

        Consumer plugins can declare that they need certain capabilities
        provided by other plugins. Override this method to declare
        what your plugin requires.

        Example: pip/pipx require "python-runtime" capability (optionally from pim).

        NOTE: Currently defaults to using the latest available instance from the provider.
        Future versions may support configuration for selecting specific instances.

        Returns:
            A list of provider requirements
        """
        return []

    @staticmethod
    def install_command(package: PackageRef) -> list[str]:
        """Returns the CLI command that would install a package.

        Override this method to provide the actual command line arguments
        that would be used to install a package. This is used for displaying
        commands in dry-run mode.

        Args:
            package: The package reference (may include a version constraint).

        Returns:
            A list of command arguments (e.g., ['pip', 'install', 'requests>=1.0']).
            Returns an empty list if the command cannot be determined.
        """
        return []

    @staticmethod
    def upgrade_command(package: PackageRef) -> list[str]:
        """Returns the CLI command that would upgrade a package.

        Override this method to provide the actual command line arguments
        that would be used to upgrade a package. This is used for displaying
        commands in dry-run mode.

        Args:
            package: The package reference (may include a version constraint).

        Returns:
            A list of command arguments (e.g., ['pip', 'install', '--upgrade', 'requests']).
        """
        return []

    @staticmethod
    def is_available() -> bool:
        """Checks if the underlying package manager is available on the system.

        Override this method to verify that the CLI tool is installed and accessible.
        The default implementation returns True.

        Returns:
            True if the package manager is available, False otherwise.
        """
        return True

    @staticmethod
    def supports_parallel() -> bool:
        """Returns whether this plugin supports parallel package installations.

        Some package managers (like pip without --no-deps) may have issues with
        concurrent installations. Override this method to return False if the
        plugin requires sequential installation.

        Returns:
            True if parallel installation is supported, False otherwise.
        """
        return True

    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package identified by its name.

        Default implementation wraps the synchronous install() in an executor.
        Override this method for true async implementations using
        asyncio.create_subprocess_exec().

        Args:
            params: The package parameters

        Returns:
            The package, or None if installation failed
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.install, params)

    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades the given package.

        Default implementation wraps the synchronous upgrade() in an executor.
        Override this method for true async implementations using
        asyncio.create_subprocess_exec().

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.upgrade, params)

    @abstractmethod
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, package: PackageRef) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            package: The package reference to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @abstractmethod
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name

        Args:
            params: The package parameters

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
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given package.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        raise NotImplementedError

    @staticmethod
    def check_updates(params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates using the plugin's native tooling.

        This method is optional. Plugins that don't support update checking
        can use the default implementation which returns an empty list.

        Args:
            params: The check parameters including which packages to check.

        Returns:
            A list of packages that have updates available. Each Package should
            have its 'version' field set to the latest available version.
        """
        return []
