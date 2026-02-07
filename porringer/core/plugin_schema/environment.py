"""Plugin utilities for package environments"""

import asyncio
import shutil
from abc import abstractmethod
from collections.abc import Callable

from pydantic import Field

from porringer.core.schema import (
    Package,
    PackageRef,
    Plugin,
    PorringerModel,
)
from porringer.schema import SubActionProgress


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
    def package_backend() -> str | None:
        """Declares which package backend (store) this plugin manages.

        Plugins that manage the same backend are interchangeable installers.
        For example, ``pip`` and ``uv`` both return ``"python"`` because they
        manage the same Python package store.  The ``BackendResolver`` picks
        the best available installer for each backend.

        Well-known backends:

        - ``"python"``        — Python packages (pip, uv)
        - ``"python-tool"``   — CLI tools installed as Python packages (pipx)
        - ``"system"``        — OS-level packages (apt, brew, winget)
        - ``"node"``          — Node.js packages (npm)
        - ``"python-runtime"``— Python runtimes themselves (pim)

        Returns ``None`` for plugins that don't participate in backend
        resolution (e.g. pure provider plugins).

        Returns:
            The backend identifier, or ``None``.
        """
        return None

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

    @classmethod
    def tool_name(cls) -> str | None:
        """Returns the CLI executable name that this plugin wraps.

        Override this method to declare which command-line tool the plugin
        uses.  The base :meth:`is_available` implementation uses this value
        with ``shutil.which`` to test whether the tool is on PATH.

        Returns ``None`` for plugins that are not backed by a single CLI
        tool (the default).  Those plugins are always considered available.

        Returns:
            The executable name (e.g. ``'pip'``, ``'uv'``), or ``None``.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Checks if the underlying package manager is available on the system.

        The default implementation delegates to :meth:`tool_name`: if a tool
        name is declared, ``shutil.which`` is used to verify the executable
        exists on PATH.  Plugins whose ``tool_name()`` returns ``None`` are
        always considered available.

        Returns:
            True if the package manager is available, False otherwise.
        """
        name = cls.tool_name()
        if name is None:
            return True
        return shutil.which(name) is not None

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
