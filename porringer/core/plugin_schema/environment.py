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


class CheckUpdatesParameters(BaseModel):
    """Parameters for checking updates via a plugin."""

    names: list[PackageName] = Field(
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
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command that would install a package.

        Override this method to provide the actual command line arguments
        that would be used to install a package. This is used for displaying
        commands in dry-run mode.

        Args:
            package: The package name to install.

        Returns:
            A list of command arguments (e.g., ['pip', 'install', 'requests']).
            Returns an empty list if the command cannot be determined.
        """
        return []

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

    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
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
