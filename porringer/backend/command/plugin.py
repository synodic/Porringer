"""The plugin command module."""

import builtins
import logging
import subprocess
import sys
from importlib import metadata

from porringer.backend.builder import Builder
from porringer.backend.resolver import resolve_list_plugins_parameters
from porringer.backend.schema import (
    PluginInstallParameters,
    PluginOperationResult,
    PluginUninstallParameters,
    PluginUpdateParameters,
)
from porringer.core.plugin_schema.environment import Environment
from porringer.schema import ListPluginResults, ListPluginsParameters
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation

logger = logging.getLogger(__name__)


class PluginCommands:
    """Plugin commands"""

    def __init__(self) -> None:
        """Initialize the PluginCommands class."""
        pass

    @staticmethod
    def list(parameters: ListPluginsParameters) -> list[ListPluginResults]:
        """Lists the plugins.

        Args:
            parameters: The list command parameters.

        Returns:
            A list of registered plugins.
        """
        logger.info('Listing plugins')

        environment_types = Builder.find_plugins('environment', Environment, check_dependencies=True)

        environments = Builder.build_plugins(environment_types)

        return resolve_list_plugins_parameters(environments)

    @staticmethod
    def _get_existing_plugin_packages() -> set[str]:
        """Get the set of package names that provide porringer.environment entry points.

        Returns:
            Set of distribution names that provide environment plugins.
        """
        packages: set[str] = set()
        for entry_point in metadata.entry_points(group='porringer.environment'):
            if entry_point.dist is not None:
                packages.add(entry_point.dist.name)
        return packages

    @staticmethod
    def install(parameters: PluginInstallParameters) -> PluginOperationResult:
        """Install a plugin package.

        Installs the specified PyPI package and validates that it provides
        a porringer.environment entry point. If validation fails, the package
        is uninstalled.

        Args:
            parameters: The install parameters including package name.

        Returns:
            PluginOperationResult indicating success or failure.

        Raises:
            PluginError: If installation fails or package is not a valid plugin.
        """
        logger.info(f'Installing plugin: {parameters.name}')

        # Get plugins before installation for comparison
        plugins_before = PluginCommands._get_existing_plugin_packages()

        # Build installation command
        if is_pipx_installation():
            args = ['pipx', 'inject', 'porringer', parameters.name]
        else:
            args = [sys.executable, '-m', 'pip', 'install', parameters.name]

        if parameters.dry:
            # For dry run, just show what would be done
            cmd_str = ' '.join(args)
            logger.info(f'Dry run: would execute: {cmd_str}')
            return PluginOperationResult(
                plugin_name=parameters.name,
                success=True,
                message=f'Would install: {cmd_str}',
            )

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.error(f'Installation failed: {result.stderr}')
                return PluginOperationResult(
                    plugin_name=parameters.name,
                    success=False,
                    message=f'Installation failed: {result.stderr.strip()}',
                )
        except FileNotFoundError as e:
            logger.error(f'Command not found: {e}')
            return PluginOperationResult(
                plugin_name=parameters.name,
                success=False,
                message=f'Command not found: {e}',
            )
        except subprocess.SubprocessError as e:
            logger.error(f'Subprocess error: {e}')
            return PluginOperationResult(
                plugin_name=parameters.name,
                success=False,
                message=f'Subprocess error: {e}',
            )

        # Validate that the package provides a porringer.environment entry point
        # We need to reload entry points to see newly installed packages
        # Clear the entry_points cache by accessing fresh metadata
        plugins_after = PluginCommands._get_existing_plugin_packages()
        new_plugins = plugins_after - plugins_before

        if not new_plugins:
            # The package installed but doesn't provide any porringer.environment entry points
            # Uninstall it and report error
            logger.warning(
                f"Package '{parameters.name}' does not provide a porringer.environment entry point. Uninstalling."
            )
            PluginCommands._uninstall_package(parameters.name)
            raise PluginError(
                f"Package '{parameters.name}' is not a valid Porringer plugin "
                '(missing porringer.environment entry point)'
            )

        logger.info(f'Successfully installed plugin: {parameters.name}')
        return PluginOperationResult(
            plugin_name=parameters.name,
            success=True,
            message=f"Successfully installed plugin '{parameters.name}'",
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
    def uninstall(parameters: PluginUninstallParameters) -> builtins.list[PluginOperationResult]:
        """Uninstall plugin packages.

        Args:
            parameters: The uninstall parameters including package names.

        Returns:
            List of PluginOperationResult for each package.
        """
        results: list[PluginOperationResult] = []

        for name in parameters.names:
            logger.info(f'Uninstalling plugin: {name}')

            # Build uninstall command
            if is_pipx_installation():
                args = ['pipx', 'uninject', 'porringer', name]
            else:
                args = [sys.executable, '-m', 'pip', 'uninstall', '-y', name]

            if parameters.dry:
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
    def update(parameters: PluginUpdateParameters) -> builtins.list[PluginOperationResult]:
        """Update plugin packages.

        Args:
            parameters: The update parameters including package names.

        Returns:
            List of PluginOperationResult for each package.
        """
        results: list[PluginOperationResult] = []

        for name in parameters.names:
            logger.info(f'Updating plugin: {name}')

            # Build update command
            if is_pipx_installation():
                args = ['pipx', 'runpip', 'porringer', 'install', '--upgrade', name]
            else:
                args = [sys.executable, '-m', 'pip', 'install', '--upgrade', name]

            if parameters.dry:
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
