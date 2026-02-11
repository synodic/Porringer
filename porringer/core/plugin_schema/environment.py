"""Plugin utilities for package environments"""

import asyncio
import logging
import re
import subprocess
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path

from packaging.version import InvalidVersion, Version
from pydantic import Field

from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import (
    Package,
    PackageRef,
    PluginParameters,
    PorringerModel,
)
from porringer.schema import SetupAction, SubActionProgress
from porringer.utility.utility import async_run_command_streaming


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


class Environment(ToolBasedPlugin):
    """Plugin definition for package environments"""

    runtime_executable: Path | None
    """Override the language runtime interpreter to target.

    When set by a `RuntimeProvider` during phased execution, installers
    use this path instead of the default interpreter on PATH.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the environment plugin.

        Args:
            parameters: Plugin parameters including distribution info
        """
        super().__init__(parameters)
        self.runtime_executable = None

    @abstractmethod
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command that would install a package.

        Override this method to provide the actual command line arguments
        that would be used to install a package.  This is used for
        displaying commands in dry-run / preview mode and should reflect
        instance state such as `runtime_executable`.

        Args:
            package: The package reference (may include a version constraint).

        Returns:
            A list of command arguments (e.g., ['pip', 'install', 'requests>=1.0']).
        """
        ...

    @abstractmethod
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command that would upgrade a package.

        Override this method to provide the actual command line arguments
        that would be used to upgrade a package.  This is used for
        displaying commands in dry-run / preview mode and should reflect
        instance state such as `runtime_executable`.

        Args:
            package: The package reference (may include a version constraint).

        Returns:
            A list of command arguments (e.g., ['pip', 'install', '--upgrade', 'requests']).
        """
        ...

    @classmethod
    def tool_version(cls) -> Version | None:
        """Returns the PEP 440 version of the underlying CLI tool.

        The default implementation runs `<tool_name> --version`, extracts the
        first version-like pattern from the combined stdout/stderr output, and
        parses it as a `Version`.

        Returns `None` when `tool_name()` is `None`, the subprocess
        fails, or the output cannot be parsed as a valid PEP 440 version.

        Subclasses may override this method if their tool's version output
        requires special parsing.

        Returns:
            The parsed tool version, or `None`.
        """
        name = cls.tool_name()
        if name is None:
            return None

        try:
            result = subprocess.run(
                [name, '--version'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = result.stdout + result.stderr
        except OSError, subprocess.SubprocessError:
            return None

        match = re.search(r'v?\d+\.\d+(?:\.\d+)*', output)
        if match is None:
            return None

        try:
            return Version(match.group(0))
        except InvalidVersion:
            return None

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

        When `params.progress_callback` is set, streams subprocess output
        line-by-line via `async_run_command_streaming()` using the
        command returned by `install_command()`.  Otherwise wraps the
        synchronous `install()` in an executor.

        Subclasses only need to override this when the streaming command
        differs from `install_command()` or when post-install logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if installation failed
        """
        if params.progress_callback is not None:
            return await self._async_streaming_run(
                args=list(self.install_command(params.package)),
                params=params,
                phase='installing',
                verb='install',
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.install, params)

    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades the given package.

        When `params.progress_callback` is set, streams subprocess output
        line-by-line via `async_run_command_streaming()` using the
        command returned by `upgrade_command()`.  Otherwise wraps the
        synchronous `upgrade()` in an executor.

        Subclasses only need to override this when the streaming command
        differs from `upgrade_command()` or when post-upgrade logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        if params.progress_callback is not None:
            return await self._async_streaming_run(
                args=list(self.upgrade_command(params.package)),
                params=params,
                phase='upgrading',
                verb='upgrade',
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.upgrade, params)

    # --- Helpers ----------------------------------------------------------

    def _make_action(self, description: str, package: PackageRef | None = None) -> SetupAction:
        """Build a `SetupAction` populated from this plugin's metadata.

        Uses `plugin_kind()`, `ecosystem()`, and `tool_name()`
        so that callers don't need to repeat these values.
        """
        return SetupAction(
            description=description,
            kind=self.plugin_kind(),
            ecosystem=self.ecosystem(),
            installer=self.tool_name(),
            package=package,
        )

    async def _async_streaming_run(
        self,
        *,
        args: list[str],
        params: PackageParameters,
        phase: str,
        verb: str,
    ) -> Package | None:
        """Run *args* with line-by-line streaming and standard error handling.

        Constructs the `SetupAction` automatically from plugin
        metadata and delegates to `async_run_command_streaming()`.

        Args:
            args: Command and arguments to run.
            params: Package parameters (must have `progress_callback` set).
            phase: Phase label for progress events (e.g. `"installing"`).
            verb: Human-readable verb for log messages (e.g. `"install"`).

        Returns:
            The installed/upgraded package, or `None` on failure.
        """
        assert params.progress_callback is not None
        logger = logging.getLogger(f'porringer.{self.tool_name()}.{verb}')
        action = self._make_action(
            description=f'{verb.capitalize()} {params.package.specifier}',
            package=params.package,
        )
        try:
            result = await async_run_command_streaming(
                args,
                action=action,
                progress_callback=params.progress_callback,
                phase=phase,
            )
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error(f'{self.tool_name()} not found')
            return None
        except Exception as e:
            logger.error(f'Failed to {verb} {params.package.name}: {e}')
            return None
        return Package(name=params.package.name, version=None)

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
