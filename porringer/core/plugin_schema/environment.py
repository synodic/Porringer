"""Plugin utilities for package environments"""

import logging
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import (
    Package,
    PackageRef,
    PluginParameters,
    PorringerModel,
)
from porringer.schema import SetupAction, SubActionProgress
from porringer.utility.utility import StreamProgress, run_command, stream_command


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

    @staticmethod
    def supports_injection() -> bool:
        """Returns whether this plugin supports injecting sub-packages.

        Injection inserts additional packages into an already-installed
        package's isolated environment.  For example, ``pipx inject``
        injects a library into a tool's venv without creating a new
        isolated environment.

        Override this to return ``True`` in plugins that support
        injection (e.g. pipx).

        Returns:
            True if injection is supported, False otherwise.
        """
        return False

    def inject_command(self, target: PackageRef, plugin: PackageRef) -> list[str]:
        """Returns the CLI command that would inject a sub-package.

        Override this method in plugins that support injection.  The
        returned command is used for dry-run / preview display.

        Args:
            target: The parent package to inject into.
            plugin: The sub-package to inject.

        Returns:
            A list of command arguments (e.g., ``['pipx', 'inject', 'pdm', 'cppython']``).

        Raises:
            NotImplementedError: If the plugin does not support injection.
        """
        raise NotImplementedError(f'{type(self).__name__} does not support injection')

    async def async_inject(self, target: PackageRef, params: PackageParameters) -> Package | None:
        """Asynchronously injects a sub-package into a parent package's environment.

        Uses a native async subprocess via ``inject_command()``.  When
        ``params.progress_callback`` is set, output is streamed
        line-by-line; otherwise output is collected silently.

        Subclasses only need to override this when the streaming command
        differs from ``inject_command()`` or when post-inject logic is
        required.

        Args:
            target: The parent package whose environment receives the injection.
            params: The package parameters (``params.package`` is the sub-package).

        Returns:
            The injected package, or ``None`` if injection failed.
        """
        args = list(self.inject_command(target, params.package))
        if params.progress_callback is not None:
            return await self._stream_command(args=args, params=params, phase='injecting', verb='inject')
        return await self._run_command(args=args, params=params, verb='inject')

    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package identified by its name.

        Uses a native async subprocess via `install_command()`.  When
        `params.progress_callback` is set, output is streamed
        line-by-line; otherwise output is collected silently.

        Subclasses only need to override this when the streaming command
        differs from `install_command()` or when post-install logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if installation failed
        """
        args = list(self.install_command(params.package))
        if params.progress_callback is not None:
            return await self._stream_command(args=args, params=params, phase='installing', verb='install')
        return await self._run_command(args=args, params=params, verb='install')

    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades the given package.

        Uses a native async subprocess via `upgrade_command()`.  When
        `params.progress_callback` is set, output is streamed
        line-by-line; otherwise output is collected silently.

        Subclasses only need to override this when the streaming command
        differs from `upgrade_command()` or when post-upgrade logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        args = list(self.upgrade_command(params.package))
        if params.progress_callback is not None:
            return await self._stream_command(args=args, params=params, phase='upgrading', verb='upgrade')
        return await self._run_command(args=args, params=params, verb='upgrade')

    # --- Helpers ----------------------------------------------------------

    def _build_action(self, description: str, package: PackageRef | None = None) -> SetupAction:
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

    async def _run_command(
        self,
        *,
        args: list[str],
        params: PackageParameters,
        verb: str,
    ) -> Package | None:
        """Run *args* as a native async subprocess without streaming.

        Replaces the legacy `run_in_executor(self.install)` pattern with
        a truly non-blocking async subprocess.

        Args:
            args: Command and arguments to run.
            params: Package parameters.
            verb: Human-readable verb for log messages (e.g. `"install"`).

        Returns:
            The installed/upgraded package, or `None` on failure.
        """
        logger = logging.getLogger(f'porringer.{self.tool_name()}.{verb}')
        try:
            result = await run_command(args)
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

    async def _stream_command(
        self,
        *,
        args: list[str],
        params: PackageParameters,
        phase: str,
        verb: str,
    ) -> Package | None:
        """Run *args* with line-by-line streaming and standard error handling.

        Constructs the `SetupAction` automatically from plugin
        metadata and delegates to `stream_command()`.

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
        action = self._build_action(
            description=f'{verb.capitalize()} {params.package.specifier}',
            package=params.package,
        )
        try:
            result = await stream_command(
                args,
                progress=StreamProgress(
                    action=action,
                    callback=params.progress_callback,
                    phase=phase,
                ),
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
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages in the given environment.

        When *project_path* is provided, plugins that manage
        project-scoped virtual environments (pip, uv) should discover
        the project's venv (e.g. ``<project_path>/.venv``) and list
        packages from that interpreter instead of the global/PATH one.
        Plugins that are inherently global (pipx, apt, brew, winget)
        may ignore this parameter.

        Args:
            project_path: Optional path to a project directory.  When
                set, the listing is scoped to the project's virtual
                environment.

        Returns:
            A list of packages
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
