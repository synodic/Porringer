"""Plugin utilities for package environments"""

import logging
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import Field

from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import (
    Package,
    PackageRef,
    PorringerModel,
)
from porringer.schema import SetupAction, SubActionProgress
from porringer.utility.utility import StreamProgress, stream_command


class PackageParameters(PorringerModel):
    """Parameters for a package install or upgrade operation."""

    package: PackageRef = Field(description='The target package')
    dry: bool = Field(
        default=False, description='If True, rehearses the operation without modifying what is actually installed'
    )
    include_prereleases: bool = Field(
        default=False,
        description='When True, allow pre-release versions during install/upgrade',
    )
    progress_callback: Callable[[SubActionProgress], None] | None = Field(
        default=None,
        exclude=True,
        description='Optional callback for reporting sub-action progress (download %, install phase, etc.)',
    )
    runtime_context: RuntimeContext | None = Field(
        default=None,
        exclude=True,
        description=(
            'Runtime context carrying resolved interpreter paths. '
            'Passed to command-generation methods so that the correct '
            'runtime is targeted without storing state on the plugin.'
        ),
    )


class CheckUpdatesParameters(PorringerModel):
    """Parameters for checking updates via a plugin."""

    packages: list[PackageRef] = Field(
        default_factory=list, description='Packages to check for updates. Empty means check all installed packages.'
    )
    include_prereleases: bool = Field(default=False, description='Include pre-release versions')
    http_client: httpx.AsyncClient | None = Field(
        default=None,
        exclude=True,
        description=(
            'Shared ``httpx.AsyncClient`` for connection pooling. '
            'When ``None`` (default), each check creates its own '
            'short-lived client.'
        ),
    )
    runtime_context: RuntimeContext | None = Field(
        default=None,
        exclude=True,
        description=(
            'Runtime context carrying resolved interpreter paths. '
            'Forwarded to ``python_command()`` so that update checks '
            'target the same interpreter as package queries.'
        ),
    )


class Environment(ToolBasedPlugin):
    """Plugin definition for package environments.

    Plugin instances are **stateless** with respect to runtime
    configuration.  A :class:`RuntimeContext` is passed explicitly to
    every method that needs to know which interpreter to target.
    """

    @abstractmethod
    def install_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command that would install a package.

        Override this method to provide the actual command line arguments
        that would be used to install a package.  This is used for
        displaying commands in dry-run / preview mode.

        Args:
            package: The package reference (may include a version constraint).
            include_prereleases: When ``True``, allow pre-release versions
                (e.g. append ``--pre`` for pip-based tools).
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use defaults (e.g. ``sys.executable``).

        Returns:
            A list of command arguments (e.g., ['pip', 'install', 'requests>=1.0']).
        """
        ...

    @abstractmethod
    def upgrade_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command that would upgrade a package.

        Override this method to provide the actual command line arguments
        that would be used to upgrade a package.  This is used for
        displaying commands in dry-run / preview mode.

        Args:
            package: The package reference (may include a version constraint).
            include_prereleases: When ``True``, allow pre-release versions
                (e.g. append ``--pre`` for pip-based tools).
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use defaults.

        Returns:
            A list of command arguments (e.g., ['pip', 'install', '--upgrade', 'requests']).
        """
        ...

    @abstractmethod
    def uninstall_command(
        self,
        package: PackageRef,
        *,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command that would uninstall a package.

        Override this method to provide the actual command line arguments
        that would be used to remove a package.  This is used for
        displaying commands in dry-run / preview mode.

        Unlike ``install_command`` and ``upgrade_command``, there is no
        ``include_prereleases`` parameter because pre-release handling
        is irrelevant when removing a package.

        Args:
            package: The package reference (only ``name`` is used).
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use defaults.

        Returns:
            A list of command arguments (e.g., ['pip', 'uninstall', '-y', 'requests']).
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

    async def install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package identified by its name.

        Uses a native async subprocess via `install_command()`.  Output
        is always streamed line-by-line; when `params.progress_callback`
        is set, progress events are emitted to the caller.

        Subclasses only need to override this when the streaming command
        differs from `install_command()` or when post-install logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if installation failed
        """
        args = list(
            self.install_command(
                params.package,
                include_prereleases=params.include_prereleases,
                runtime_context=params.runtime_context,
            )
        )
        return await self._execute_command(args=args, params=params, phase='installing', verb='install')

    async def upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades the given package.

        Uses a native async subprocess via `upgrade_command()`.  Output
        is always streamed line-by-line; when `params.progress_callback`
        is set, progress events are emitted to the caller.

        Subclasses only need to override this when the streaming command
        differs from `upgrade_command()` or when post-upgrade logic
        (e.g. version retrieval) is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the upgrade failed.
        """
        args = list(
            self.upgrade_command(
                params.package,
                include_prereleases=params.include_prereleases,
                runtime_context=params.runtime_context,
            )
        )
        return await self._execute_command(args=args, params=params, phase='upgrading', verb='upgrade')

    async def uninstall(self, params: PackageParameters) -> Package | None:
        """Asynchronously uninstalls the given package.

        Uses a native async subprocess via `uninstall_command()`.  Output
        is always streamed line-by-line; when `params.progress_callback`
        is set, progress events are emitted to the caller.

        Subclasses only need to override this when the streaming command
        differs from `uninstall_command()` or when post-uninstall logic
        is required.

        Args:
            params: The package parameters

        Returns:
            The package, or None if the uninstall failed.
        """
        args = list(self.uninstall_command(params.package, runtime_context=params.runtime_context))
        uninstall_logger = logging.getLogger(f'porringer.{self.tool_name()}.uninstall')
        uninstall_logger.debug('uninstall command: %s', args)
        return await self._execute_command(args=args, params=params, phase='uninstalling', verb='uninstall')

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

    async def _execute_command(
        self,
        *,
        args: list[str],
        params: PackageParameters,
        phase: str,
        verb: str,
    ) -> Package | None:
        """Run *args* as an async subprocess with line-by-line streaming.

        When ``params.progress_callback`` is set, progress events are
        emitted to the caller.  When it is ``None``, output is still
        collected via streaming but no progress events are emitted.

        Args:
            args: Command and arguments to run.
            params: Package parameters.
            phase: Phase label for progress events (e.g. ``"installing"``).
            verb: Human-readable verb for log messages (e.g. ``"install"``).

        Returns:
            The installed/upgraded package, or ``None`` on failure.
        """
        logger = logging.getLogger(f'porringer.{self.tool_name()}.{verb}')
        action = self._build_action(
            description=f'{verb.capitalize()} {params.package.specifier}',
            package=params.package,
        )
        callback = params.progress_callback if params.progress_callback is not None else lambda _: None
        try:
            transformed = self._transport.transform_args(args)
            result = await stream_command(
                transformed,
                progress=StreamProgress(
                    action=action,
                    callback=callback,
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
    async def packages(
        self,
        *,
        project_path: Path | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> list[Package]:
        """Gathers installed packages in the given environment.

        When *project_path* is provided, plugins that manage
        project-scoped virtual environments (pip, uv) should discover
        the project's venv (e.g. ``<project_path>/.venv``) and list
        packages from that interpreter instead of the global/PATH one.
        Plugins that are inherently global (pipx, apt, brew, winget)
        may ignore this parameter.

        When *runtime_context* is provided, Python-ecosystem plugins
        should target the resolved interpreter instead of
        ``sys.executable``.

        Implementations should use the async helper methods
        (``_run_json_command``, ``_run_text_command``) instead of
        ``subprocess.run`` so the event loop is never blocked.

        Args:
            project_path: Optional path to a project directory.  When
                set, the listing is scoped to the project's virtual
                environment.
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use defaults.

        Returns:
            A list of packages
        """
        raise NotImplementedError

    @abstractmethod
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Check for available updates using the plugin's native tooling.

        Every ``Environment`` subclass **must** implement this method.
        Plugins that genuinely cannot check for updates should return
        an empty list.

        Use the shared helpers ``_check_npm_registry()`` or
        ``PythonEnvironment._check_pypi_updates()`` where applicable.

        Implementations should use async I/O (``_run_json_command``,
        ``_run_text_command``, ``httpx.AsyncClient``) instead of
        blocking calls so the event loop is never blocked.

        Args:
            params: The check parameters including which packages to check.

        Returns:
            A list of packages that have updates available. Each Package
            should have its ``version`` field set to the latest
            available version.
        """
        ...

    @staticmethod
    async def _check_npm_registry(
        packages: list[PackageRef],
        *,
        include_prereleases: bool = False,
        logger: logging.Logger | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[Package]:
        """Query the npm registry for the latest versions of the given packages.

        Shared helper for plugins that install from the npm registry
        (npm, pnpm, bun, and the npm branch of deno).

        Uses ``httpx.AsyncClient`` so the event loop is never blocked
        by network I/O.

        For each package, fetches
        ``https://registry.npmjs.org/{name}`` and extracts:

        * **stable** — ``dist-tags.latest``
        * **pre-release** — the last key in ``versions`` (highest semver)

        Args:
            packages: Package references to look up.
            include_prereleases: When ``True``, return pre-release versions.
            logger: Optional logger for debug messages. Falls back to
                ``logging.getLogger('porringer.npm_registry')``.
            http_client: Shared ``httpx.AsyncClient`` for connection pooling.
                When ``None``, a short-lived client is created per call.

        Returns:
            A list of packages with their latest available version.
        """
        if logger is None:
            logger = logging.getLogger('porringer.npm_registry')

        results: list[Package] = []

        async def _run(client: httpx.AsyncClient) -> list[Package]:
            inner: list[Package] = []
            for pkg_ref in packages:
                try:
                    response = await client.get(f'https://registry.npmjs.org/{pkg_ref.name}')
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    logger.debug('npm registry query failed for %s: %s', pkg_ref.name, exc)
                    continue

                if include_prereleases:
                    versions = data.get('versions', {})
                    if versions:
                        latest = list(versions.keys())[-1]
                        inner.append(Package(name=pkg_ref.name, version=latest))
                else:
                    dist_tags = data.get('dist-tags', {})
                    latest = dist_tags.get('latest')
                    if latest:
                        inner.append(Package(name=pkg_ref.name, version=latest))
            return inner

        if http_client is not None:
            results = await _run(http_client)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                results = await _run(client)

        return results
