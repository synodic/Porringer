"""CLI command implementation for plugin.

The plugin command module.

Manages *porringer extension* packages — installing, upgrading, and
uninstalling plugins that extend porringer's capabilities (e.g.
``porringer-plugin-apt``).  For operations on packages *managed by*
plugins (e.g. ``requests`` via pip), see :mod:`.package`.
"""

import asyncio
import builtins
import logging
import sys
from importlib import metadata

from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_environments
from porringer.backend.resolver import build_plugin_info
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import PluginInfo, PluginOperationResult
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation, run_command

logger = logging.getLogger(__name__)


class PluginCommands:
    """Extension package commands.

    All methods are static — the class acts as a namespace and does
    not require instantiation.  Use `PluginCommands.list()` directly
    or via an `API` instance.
    """

    @staticmethod
    async def list(
        *,
        kinds: builtins.list[PluginKind] | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
        include_managed: bool = False,
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

        When *include_managed* is ``True``, each :class:`PluginManager`
        plugin is queried for its natively installed sub-plugins.
        These appear as additional :class:`PluginInfo` entries whose
        :attr:`~PluginInfo.host_tool` is set to the manager's tool
        name.

        Args:
            kinds: Only include plugins matching these kinds. `None` returns all.
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            runtime_context: Pre-resolved runtime context.  When
                ``None``, a context is resolved automatically from
                available runtime providers.
            include_managed: When ``True``, include sub-plugins reported
                by each :class:`PluginManager` plugin.

        Returns:
            A list of registered plugins, optionally filtered by kind.
        """
        logger.debug('Listing plugins')

        if plugins is not None:
            environments = plugins.environments
            projects = plugins.project_environments
            scm_plugins = plugins.scm_environments
            runtime_context = plugins.resolved_runtime(runtime_context)
        else:
            environments = discover_environments()

            # Project-environment plugins (project sync)
            project_types, _ = Builder.find_plugins('project_environment', ProjectEnvironment)
            project_instances = Builder.build_plugins(project_types)
            projects = {info.name: inst for info, inst in zip(project_types, project_instances, strict=True)}

            # SCM plugins (source control)
            scm_types, _ = Builder.find_plugins('scm', ScmEnvironment)
            scm_instances = Builder.build_plugins(scm_types)
            scm_plugins = {info.name: inst for info, inst in zip(scm_types, scm_instances, strict=True)}

        # Auto-resolve runtime context when the caller did not supply one.
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        all_plugins: dict[str, Plugin] = {**environments, **projects, **scm_plugins}

        results = build_plugin_info(all_plugins, kinds=kinds, runtime_context=runtime_context)

        if include_managed:
            managers: list[tuple[str, PluginManager]] = [
                (name, plugin) for name, plugin in all_plugins.items() if isinstance(plugin, PluginManager)
            ]
            if managers:
                managed_results: list[PluginInfo] = []

                async def _query_manager(name: str, mgr: PluginManager) -> builtins.list[PluginInfo]:
                    tool = mgr.tool_name()
                    sub_pkgs = await mgr.installed_plugins()
                    parent = next(r for r in results if r.name == name)
                    return [
                        PluginInfo(
                            name=pkg.name,
                            kind=parent.kind,
                            version=parent.version,
                            installed=True,
                            tool_version=None,
                            host_tool=tool,
                        )
                        for pkg in sub_pkgs
                    ]

                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(_query_manager(name, mgr)) for name, mgr in managers]
                for task in tasks:
                    managed_results.extend(task.result())
                results.extend(managed_results)

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
    def _build_upgrade_args(name: str) -> builtins.list[str]:
        """Build the upgrade command for a plugin package."""
        if is_pipx_installation():
            return ['pipx', 'runpip', 'porringer', 'install', '--upgrade', name]
        return [sys.executable, '-m', 'pip', 'install', '--upgrade', name]

    @staticmethod
    async def _run_plugin_operation(
        name: str,
        args: builtins.list[str],
        *,
        verb: str,
        dry_run: bool,
        timeout_seconds: int,
    ) -> PluginOperationResult:
        """Execute a plugin subprocess operation with dry-run support.

        Centralises the dry-run check, subprocess invocation, error
        handling, and result construction shared by install / uninstall /
        upgrade.

        Args:
            name: Plugin package name.
            args: Full command-line arguments.
            verb: Human-readable verb (``"install"``, ``"uninstall"``, ``"upgrade"``).
            dry_run: If ``True``, only report what would be done.
            timeout_seconds: Subprocess timeout in seconds.

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
            result = await run_command(args, timeout=timeout_seconds)
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
        except (OSError, TimeoutError) as e:
            logger.error('Subprocess error: %s', e)
            return PluginOperationResult(
                plugin_name=name,
                success=False,
                message=f'Subprocess error: {e}',
            )

    @staticmethod
    async def install(name: str, *, dry_run: bool = False) -> PluginOperationResult:
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
            return await PluginCommands._run_plugin_operation(
                name,
                args,
                verb='install',
                dry_run=True,
                timeout_seconds=120,
            )

        result = await PluginCommands._run_plugin_operation(
            name,
            args,
            verb='install',
            dry_run=False,
            timeout_seconds=120,
        )
        if not result.success:
            return result

        # Validate that the package provides a porringer plugin entry point
        plugins_after = PluginCommands._get_existing_plugin_packages()
        new_plugins = plugins_after - plugins_before

        if not new_plugins:
            logger.warning("Package '%s' does not provide a porringer plugin entry point. Uninstalling.", name)
            await PluginCommands._uninstall_package(name)
            groups = ', '.join(PluginCommands._PLUGIN_GROUPS)
            raise PluginError(f"Package '{name}' is not a valid Porringer plugin (no entry point in {groups})")

        return result

    @staticmethod
    async def _uninstall_package(name: str) -> None:
        """Internal helper to uninstall a package.

        Args:
            name: Package name to uninstall.

        Returns:
            None.
        """
        args = PluginCommands._build_uninstall_args(name)
        await run_command(args, timeout=60)

    @staticmethod
    async def uninstall(names: builtins.list[str], *, dry_run: bool = False) -> builtins.list[PluginOperationResult]:
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
                await PluginCommands._run_plugin_operation(
                    name,
                    args,
                    verb='uninstall',
                    dry_run=dry_run,
                    timeout_seconds=60,
                )
            )

        return results

    @staticmethod
    async def upgrade(names: builtins.list[str], *, dry_run: bool = False) -> builtins.list[PluginOperationResult]:
        """Upgrade plugin packages.

        Args:
            names: Package names to upgrade.
            dry_run: If `True`, only report what would be done.

        Returns:
            List of PluginOperationResult for each package.
        """
        results: list[PluginOperationResult] = []

        for name in names:
            logger.info('Upgrading plugin: %s', name)
            args = PluginCommands._build_upgrade_args(name)
            results.append(
                await PluginCommands._run_plugin_operation(
                    name,
                    args,
                    verb='upgrade',
                    dry_run=dry_run,
                    timeout_seconds=120,
                )
            )

        return results
