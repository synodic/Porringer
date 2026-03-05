"""The plugin command module."""

import asyncio
import builtins
import logging
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from packaging.utils import canonicalize_name

from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.resolver import build_plugin_info
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Package, Plugin, PluginKind
from porringer.schema import PluginInfo, PluginOperationResult, RuntimePackageResult
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation

logger = logging.getLogger(__name__)


class PluginCommands:
    """Plugin commands.

    All methods are static — the class acts as a namespace and does
    not require instantiation.  Use `PluginCommands.list()` directly
    or via an `API` instance.
    """

    @staticmethod
    def _discover_environments() -> dict[str, Environment]:
        """Discover and build all environment plugins.

        Returns:
            Name-keyed dict of environment plugins with dependencies resolved.
        """
        environment_types = Builder.find_plugins('environment', Environment, check_dependencies=True)
        instances = Builder.build_plugins(environment_types)
        return {info.name: inst for info, inst in zip(environment_types, instances, strict=True)}

    @staticmethod
    async def list(
        *,
        kinds: builtins.list[PluginKind] | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> builtins.list[PluginInfo]:
        """Lists all registered plugins across every plugin group.

        Discovers `environment` (package / tool / runtime),
        `project_environment` (project sync), and `scm` (source control)
        plugins.  Results can be filtered by `kinds`.

        When *plugins* is provided, its environments, project
        environments, and SCM plugins are used directly — no
        entry-point scanning is performed.  ``runtime_context`` is
        extracted from ``plugins.runtime_context`` unless an explicit
        value is supplied.

        Args:
            kinds: Only include plugins matching these kinds. `None` returns all.
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            runtime_context: Pre-resolved runtime context.  When
                ``None``, a context is resolved automatically from
                available runtime providers.

        Returns:
            A list of registered plugins, optionally filtered by kind.
        """
        logger.debug('Listing plugins')

        if plugins is not None:
            environments = plugins.environments
            projects = plugins.project_environments
            scm_plugins = plugins.scm_environments
            if runtime_context is None:
                runtime_context = plugins.runtime_context
        else:
            environments = PluginCommands._discover_environments()

            # Project-environment plugins (project sync)
            project_types = Builder.find_plugins('project_environment', ProjectEnvironment)
            project_instances = Builder.build_plugins(project_types)
            projects = {info.name: inst for info, inst in zip(project_types, project_instances, strict=True)}

            # SCM plugins (source control)
            scm_types = Builder.find_plugins('scm', ScmEnvironment)
            scm_instances = Builder.build_plugins(scm_types)
            scm_plugins = {info.name: inst for info, inst in zip(scm_types, scm_instances, strict=True)}

        # Auto-resolve runtime context when the caller did not supply one.
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        all_plugins: dict[str, Plugin] = {**environments, **projects, **scm_plugins}

        return build_plugin_info(all_plugins, kinds=kinds, runtime_context=runtime_context)

    @staticmethod
    async def list_packages(
        plugin_name: str,
        project_path: Path | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> builtins.list[Package]:
        """List packages installed in a plugin's environment.

        Discovers the named plugin among `environment` plugins,
        initialises it, and returns the packages it reports as installed.

        When *plugins* is provided, the named environment is looked up
        directly from ``plugins.environments`` — no entry-point
        scanning is performed.  ``runtime_context`` is extracted from
        ``plugins.runtime_context`` unless an explicit value is supplied.

        When *project_path* is a directory, it is forwarded to the
        plugin's `packages()` method so that venv-scoped plugins
        (pip, uv) can discover the project's virtual environment and
        list packages from that interpreter.  Globally-scoped plugins
        (pipx, apt, brew) ignore the parameter.

        Args:
            plugin_name: The canonical plugin name to query.
            project_path: Path to the project directory.  `None` queries
                the global / default environment.
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            runtime_context: Pre-resolved runtime context.  When
                ``None``, a context is resolved automatically from
                available runtime providers.

        Returns:
            The packages managed by the named plugin.

        Raises:
            PluginError: If the plugin is not found.
        """
        logger.debug('Listing packages for plugin: %s', plugin_name)

        if plugins is not None:
            environments = plugins.environments
            if runtime_context is None:
                runtime_context = plugins.runtime_context
        else:
            environments = PluginCommands._discover_environments()

        # Auto-resolve runtime context when the caller did not supply one.
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        # Keys from _discover_environments() are already PEP 503-canonicalised.
        env = environments.get(str(canonicalize_name(plugin_name)))
        if env is None:
            available = sorted(environments.keys())
            raise PluginError(f"Plugin '{plugin_name}' not found. Available: {', '.join(available)}")

        if not env.query_availability(runtime_context):
            logger.debug("Plugin '%s' is not available; returning empty package list", plugin_name)
            return []
        return await env.packages(project_path=project_path, runtime_context=runtime_context)

    @staticmethod
    async def list_packages_by_runtime(
        plugin_name: str,
        *,
        project_path: Path | None = None,
        plugins: DiscoveredPlugins | None = None,
    ) -> builtins.list[RuntimePackageResult]:
        """List packages for every installed runtime of a consumer plugin.

        Discovers all runtime tags from available
        :class:`~porringer.core.plugin_schema.runtime.RuntimeProvider`
        plugins, resolves each to an interpreter executable, and
        queries the named plugin's ``packages()`` method against every
        matching runtime.  Results are returned tagged by provider,
        version tag, and executable path.

        Availability checks and package queries for each runtime run
        concurrently via :func:`asyncio.gather`.

        When *plugins* is provided, its environments are used
        directly — no entry-point scanning is performed.

        Args:
            plugin_name: The canonical plugin name to query (must be a
                :class:`~porringer.core.plugin_schema.runtime.RuntimeConsumer`).
            project_path: Path to the project directory.  ``None``
                queries the global / default environment.
            plugins: Pre-discovered plugins from
                :meth:`API.discover_plugins
                <porringer.api.API.discover_plugins>`.

        Returns:
            A list of :class:`RuntimePackageResult` entries, one per
            successfully queried runtime, ordered by descending tag
            version.

        Raises:
            PluginError: If the plugin is not found or is not a
                :class:`RuntimeConsumer`.
        """
        logger.debug('Listing packages by runtime for plugin: %s', plugin_name)

        environments = plugins.environments if plugins is not None else PluginCommands._discover_environments()

        # Look up the target environment
        key = str(canonicalize_name(plugin_name))
        if key not in environments:
            available = sorted(environments.keys())
            raise PluginError(f"Plugin '{plugin_name}' not found. Available: {', '.join(available)}")

        env = environments[key]

        if not isinstance(env, RuntimeConsumer):
            raise PluginError(f"Plugin '{plugin_name}' is not a RuntimeConsumer and cannot be queried per-runtime")

        consumed_kind = env.consumed_runtime_kind()

        # Resolve every runtime tag across all providers
        all_runtimes = await Builder.resolve_all_runtime_executables(environments)

        # Filter to the kind consumed by the target plugin
        matching = [rt for rt in all_runtimes if rt.kind == consumed_kind]
        if not matching:
            logger.debug("No runtimes of kind '%s' found for plugin '%s'", consumed_kind, plugin_name)
            return []

        # Query availability + packages concurrently per runtime
        async def _query(rt) -> RuntimePackageResult | None:
            ctx = RuntimeContext(executables={rt.kind: rt.executable})
            if not env.query_availability(ctx):
                logger.debug(
                    "Plugin '%s' not available for runtime %s (tag=%s)",
                    plugin_name,
                    rt.executable,
                    rt.tag,
                )
                return None
            pkgs = await env.packages(project_path=project_path, runtime_context=ctx)
            return RuntimePackageResult(
                provider=rt.provider,
                tag=rt.tag,
                executable=rt.executable,
                packages=pkgs,
            )

        gathered = await asyncio.gather(*[_query(rt) for rt in matching])
        results = [r for r in gathered if r is not None]

        logger.debug(
            'list_packages_by_runtime complete: %d runtime(s) queried for %s',
            len(results),
            plugin_name,
        )
        return results

    _PLUGIN_GROUPS = (
        'porringer.environment',
        'porringer.project_environment',
        'porringer.scm',
    )

    @staticmethod
    def _get_existing_plugin_packages() -> set[str]:
        """Get the set of package names that provide any porringer plugin entry point.

        Returns:
            Set of distribution names that provide porringer plugins.
        """
        packages: set[str] = set()
        for group in PluginCommands._PLUGIN_GROUPS:
            for entry_point in metadata.entry_points(group=group):
                if entry_point.dist is not None:
                    packages.add(entry_point.dist.name)
        return packages

    @staticmethod
    def _build_install_args(name: str) -> builtins.list[str]:
        """Build the install command for a plugin package."""
        if is_pipx_installation():
            return ['pipx', 'inject', 'porringer', name]
        return [sys.executable, '-m', 'pip', 'install', name]

    @staticmethod
    def _build_uninstall_args(name: str) -> builtins.list[str]:
        """Build the uninstall command for a plugin package."""
        if is_pipx_installation():
            return ['pipx', 'uninject', 'porringer', name]
        return [sys.executable, '-m', 'pip', 'uninstall', '-y', name]

    @staticmethod
    def _build_update_args(name: str) -> builtins.list[str]:
        """Build the update command for a plugin package."""
        if is_pipx_installation():
            return ['pipx', 'runpip', 'porringer', 'install', '--upgrade', name]
        return [sys.executable, '-m', 'pip', 'install', '--upgrade', name]

    @staticmethod
    def _run_plugin_operation(
        name: str,
        args: builtins.list[str],
        *,
        verb: str,
        dry_run: bool,
        timeout: int,
    ) -> PluginOperationResult:
        """Execute a plugin subprocess operation with dry-run support.

        Centralises the dry-run check, subprocess invocation, error
        handling, and result construction shared by install / uninstall /
        update.

        Args:
            name: Plugin package name.
            args: Full command-line arguments.
            verb: Human-readable verb (``"install"``, ``"uninstall"``, ``"update"``).
            dry_run: If ``True``, only report what would be done.
            timeout: Subprocess timeout in seconds.

        Returns:
            PluginOperationResult indicating outcome.
        """
        past = f'{verb}ed' if not verb.endswith('e') else f'{verb}d'

        if dry_run:
            cmd_str = ' '.join(args)
            logger.info('Dry run: would execute: %s', cmd_str)
            return PluginOperationResult(
                plugin_name=name,
                success=True,
                message=f'Would {verb}: {cmd_str}',
            )

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
            if result.returncode != 0:
                logger.error('%s failed for %s: %s', verb.capitalize(), name, result.stderr)
                return PluginOperationResult(
                    plugin_name=name,
                    success=False,
                    message=f'{verb.capitalize()} failed: {result.stderr.strip()}',
                )
            logger.info('Successfully %s plugin: %s', past, name)
            return PluginOperationResult(
                plugin_name=name,
                success=True,
                message=f"Successfully {past} plugin '{name}'",
            )
        except FileNotFoundError as e:
            logger.error('Command not found: %s', e)
            return PluginOperationResult(
                plugin_name=name,
                success=False,
                message=f'Command not found: {e}',
            )
        except subprocess.SubprocessError as e:
            logger.error('Subprocess error: %s', e)
            return PluginOperationResult(
                plugin_name=name,
                success=False,
                message=f'Subprocess error: {e}',
            )

    @staticmethod
    def install(name: str, *, dry_run: bool = False) -> PluginOperationResult:
        """Install a plugin package.

        Installs the specified PyPI package and validates that it provides
        a porringer plugin entry point. If validation fails, the package
        is uninstalled.

        Args:
            name: PyPI package name to install.
            dry_run: If `True`, only report what would be done.

        Returns:
            PluginOperationResult indicating success or failure.

        Raises:
            PluginError: If installation fails or package is not a valid plugin.
        """
        logger.info('Installing plugin: %s', name)

        # Get plugins before installation for comparison
        plugins_before = PluginCommands._get_existing_plugin_packages()

        args = PluginCommands._build_install_args(name)

        if dry_run:
            return PluginCommands._run_plugin_operation(name, args, verb='install', dry_run=True, timeout=120)

        result = PluginCommands._run_plugin_operation(name, args, verb='install', dry_run=False, timeout=120)
        if not result.success:
            return result

        # Validate that the package provides a porringer plugin entry point
        plugins_after = PluginCommands._get_existing_plugin_packages()
        new_plugins = plugins_after - plugins_before

        if not new_plugins:
            logger.warning("Package '%s' does not provide a porringer plugin entry point. Uninstalling.", name)
            PluginCommands._uninstall_package(name)
            groups = ', '.join(PluginCommands._PLUGIN_GROUPS)
            raise PluginError(f"Package '{name}' is not a valid Porringer plugin (no entry point in {groups})")

        return result

    @staticmethod
    def _uninstall_package(name: str) -> subprocess.CompletedProcess[str]:
        """Internal helper to uninstall a package.

        Args:
            name: Package name to uninstall.

        Returns:
            The completed process result.
        """
        args = PluginCommands._build_uninstall_args(name)
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)

    @staticmethod
    def uninstall(names: builtins.list[str], *, dry_run: bool = False) -> builtins.list[PluginOperationResult]:
        """Uninstall plugin packages.

        Args:
            names: Package names to uninstall.
            dry_run: If `True`, only report what would be done.

        Returns:
            List of PluginOperationResult for each package.
        """
        results: list[PluginOperationResult] = []

        for name in names:
            logger.info('Uninstalling plugin: %s', name)
            args = PluginCommands._build_uninstall_args(name)
            results.append(
                PluginCommands._run_plugin_operation(name, args, verb='uninstall', dry_run=dry_run, timeout=60)
            )

        return results

    @staticmethod
    def update(names: builtins.list[str], *, dry_run: bool = False) -> builtins.list[PluginOperationResult]:
        """Update plugin packages.

        Args:
            names: Package names to update.
            dry_run: If `True`, only report what would be done.

        Returns:
            List of PluginOperationResult for each package.
        """
        results: list[PluginOperationResult] = []

        for name in names:
            logger.info('Updating plugin: %s', name)
            args = PluginCommands._build_update_args(name)
            results.append(
                PluginCommands._run_plugin_operation(name, args, verb='update', dry_run=dry_run, timeout=120)
            )

        return results
