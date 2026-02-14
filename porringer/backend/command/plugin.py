"""The plugin command module."""

import builtins
import logging
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from porringer.backend.builder import Builder
from porringer.backend.resolver import build_plugin_info
from porringer.backend.schema import PluginOperationResult
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Package, Plugin, PluginKind
from porringer.schema import PluginInfo
from porringer.utility.exception import PluginError
from porringer.utility.utility import canonicalize_type, is_pipx_installation

logger = logging.getLogger(__name__)


class PluginCommands:
    """Plugin commands.

    All methods are static — the class acts as a namespace and does
    not require instantiation.  Use ``PluginCommands.list()`` directly
    or via an ``API`` instance.
    """

    @staticmethod
    def _discover_environments() -> builtins.list[Environment]:
        """Discover and build all environment plugins.

        Returns:
            Instantiated environment plugins with dependencies resolved.
        """
        environment_types = Builder.find_plugins('environment', Environment, check_dependencies=True)
        return Builder.build_plugins(environment_types)

    @staticmethod
    def list(*, kinds: builtins.list[PluginKind] | None = None) -> builtins.list[PluginInfo]:
        """Lists all registered plugins across every plugin group.

        Discovers `environment` (package / tool / runtime),
        `project_environment` (project sync), and `scm` (source control)
        plugins.  Results can be filtered by `kinds`.

        Args:
            kinds: Only include plugins matching these kinds. `None` returns all.

        Returns:
            A list of registered plugins, optionally filtered by kind.
        """
        logger.info('Listing plugins')

        environments = PluginCommands._discover_environments()

        # Project-environment plugins (project sync)
        project_types = Builder.find_plugins('project_environment', ProjectEnvironment)
        projects = Builder.build_plugins(project_types)

        # SCM plugins (source control)
        scm_types = Builder.find_plugins('scm', ScmEnvironment)
        scm_plugins = Builder.build_plugins(scm_types)

        all_plugins: builtins.list[Plugin] = [*environments, *projects, *scm_plugins]

        return build_plugin_info(all_plugins, kinds=kinds)

    @staticmethod
    def list_packages(plugin_name: str, project_path: Path | None = None) -> builtins.list[Package]:
        """List packages installed in a plugin's environment.

        Discovers the named plugin among `environment` plugins,
        initialises it, and returns the packages it reports as installed.

        When *project_path* is a directory, it is forwarded to the
        plugin's ``packages()`` method so that venv-scoped plugins
        (pip, uv) can discover the project's virtual environment and
        list packages from that interpreter.  Globally-scoped plugins
        (pipx, apt, brew) ignore the parameter.

        Args:
            plugin_name: The canonical plugin name to query.
            project_path: Path to the project directory.  ``None`` queries
                the global / default environment.

        Returns:
            The packages managed by the named plugin.

        Raises:
            PluginError: If the plugin is not found or not available.
        """
        logger.info(f'Listing packages for plugin: {plugin_name}')

        environments = PluginCommands._discover_environments()

        for env in environments:
            canonicalized = canonicalize_type(type(env))
            if canonicalized.name == plugin_name:
                if not type(env).is_available():
                    raise PluginError(f"Plugin '{plugin_name}' is not available on this system")
                return env.packages(project_path=project_path)

        available = [canonicalize_type(type(e)).name for e in environments]
        raise PluginError(f"Plugin '{plugin_name}' not found. Available: {', '.join(sorted(available))}")

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
        logger.info(f'Installing plugin: {name}')

        # Get plugins before installation for comparison
        plugins_before = PluginCommands._get_existing_plugin_packages()

        # Build installation command
        if is_pipx_installation():
            args = ['pipx', 'inject', 'porringer', name]
        else:
            args = [sys.executable, '-m', 'pip', 'install', name]

        if dry_run:
            # For dry run, just show what would be done
            cmd_str = ' '.join(args)
            logger.info(f'Dry run: would execute: {cmd_str}')
            return PluginOperationResult(
                plugin_name=name,
                success=True,
                message=f'Would install: {cmd_str}',
            )

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.error(f'Installation failed: {result.stderr}')
                return PluginOperationResult(
                    plugin_name=name,
                    success=False,
                    message=f'Installation failed: {result.stderr.strip()}',
                )
        except FileNotFoundError as e:
            logger.error(f'Command not found: {e}')
            return PluginOperationResult(
                plugin_name=name,
                success=False,
                message=f'Command not found: {e}',
            )
        except subprocess.SubprocessError as e:
            logger.error(f'Subprocess error: {e}')
            return PluginOperationResult(
                plugin_name=name,
                success=False,
                message=f'Subprocess error: {e}',
            )

        # Validate that the package provides a porringer plugin entry point
        plugins_after = PluginCommands._get_existing_plugin_packages()
        new_plugins = plugins_after - plugins_before

        if not new_plugins:
            logger.warning(f"Package '{name}' does not provide a porringer plugin entry point. Uninstalling.")
            PluginCommands._uninstall_package(name)
            raise PluginError(
                f"Package '{name}' is not a valid Porringer plugin (no entry point in {', '.join(PluginCommands._PLUGIN_GROUPS)})"
            )

        logger.info(f'Successfully installed plugin: {name}')
        return PluginOperationResult(
            plugin_name=name,
            success=True,
            message=f"Successfully installed plugin '{name}'",
        )

    @staticmethod
    def _uninstall_package(name: str) -> subprocess.CompletedProcess[str]:
        """Internal helper to uninstall a package.

        Args:
            name: Package name to uninstall.

        Returns:
            The completed process result.
        """
        if is_pipx_installation():
            args = ['pipx', 'uninject', 'porringer', name]
        else:
            args = [sys.executable, '-m', 'pip', 'uninstall', '-y', name]

        return subprocess.run(args, capture_output=True, text=True, check=False)

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
            logger.info(f'Uninstalling plugin: {name}')

            # Build uninstall command
            if is_pipx_installation():
                args = ['pipx', 'uninject', 'porringer', name]
            else:
                args = [sys.executable, '-m', 'pip', 'uninstall', '-y', name]

            if dry_run:
                cmd_str = ' '.join(args)
                logger.info(f'Dry run: would execute: {cmd_str}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=True,
                        message=f'Would uninstall: {cmd_str}',
                    )
                )
                continue

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    logger.error(f'Uninstall failed for {name}: {result.stderr}')
                    results.append(
                        PluginOperationResult(
                            plugin_name=name,
                            success=False,
                            message=f'Uninstall failed: {result.stderr.strip()}',
                        )
                    )
                else:
                    logger.info(f'Successfully uninstalled plugin: {name}')
                    results.append(
                        PluginOperationResult(
                            plugin_name=name,
                            success=True,
                            message=f"Successfully uninstalled plugin '{name}'",
                        )
                    )
            except FileNotFoundError as e:
                logger.error(f'Command not found: {e}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=False,
                        message=f'Command not found: {e}',
                    )
                )
            except subprocess.SubprocessError as e:
                logger.error(f'Subprocess error: {e}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=False,
                        message=f'Subprocess error: {e}',
                    )
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
            logger.info(f'Updating plugin: {name}')

            # Build update command
            if is_pipx_installation():
                args = ['pipx', 'runpip', 'porringer', 'install', '--upgrade', name]
            else:
                args = [sys.executable, '-m', 'pip', 'install', '--upgrade', name]

            if dry_run:
                cmd_str = ' '.join(args)
                logger.info(f'Dry run: would execute: {cmd_str}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=True,
                        message=f'Would update: {cmd_str}',
                    )
                )
                continue

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    logger.error(f'Update failed for {name}: {result.stderr}')
                    results.append(
                        PluginOperationResult(
                            plugin_name=name,
                            success=False,
                            message=f'Update failed: {result.stderr.strip()}',
                        )
                    )
                else:
                    logger.info(f'Successfully updated plugin: {name}')
                    results.append(
                        PluginOperationResult(
                            plugin_name=name,
                            success=True,
                            message=f"Successfully updated plugin '{name}'",
                        )
                    )
            except FileNotFoundError as e:
                logger.error(f'Command not found: {e}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=False,
                        message=f'Command not found: {e}',
                    )
                )
            except subprocess.SubprocessError as e:
                logger.error(f'Subprocess error: {e}')
                results.append(
                    PluginOperationResult(
                        plugin_name=name,
                        success=False,
                        message=f'Subprocess error: {e}',
                    )
                )

        return results
