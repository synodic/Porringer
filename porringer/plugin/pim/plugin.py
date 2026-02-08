"""Plugin implementation for Python Install Manager (pymanager)"""

import json
import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef, PluginDependency


class PimEnvironment(Environment):
    """Represents a Python runtime environment managed by Python Install Manager (pymanager).

    Provides methods to install, search, uninstall, upgrade, and list Python runtimes using
    the official Python Install Manager (py/pymanager) as the backend.

    This plugin is Windows-only and requires winget to be available for installing
    the Python Install Manager itself.

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions.

    CLI Reference:
        - py list [-f=<FMT>] [--online] [<TAG>...]  - List installed/available runtimes
        - py install [-f|--force] [-u|--update] [--dry-run] [<TAG>...]  - Install runtimes
        - py uninstall [-y|--yes] <TAG>...  - Uninstall runtimes
        - pymanager install 9NQ7512CXL7T  - Install via winget (Store app ID)
    """

    @staticmethod
    @override
    def package_backend() -> str:
        """PIM manages the ``python-runtime`` package backend."""
        return 'python-runtime'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """PIM wraps the ``py`` CLI."""
        return 'py'

    @staticmethod
    @override
    def dependencies() -> list[PluginDependency]:
        """Declares plugin dependencies.

        This plugin requires Windows and depends on winget when on Windows
        for installing the Python Install Manager.

        Returns:
            A list of plugin dependencies
        """
        return [
            PluginDependency(
                plugin='winget',
                required=True,
                platforms=['win32'],
            ),
        ]

    @staticmethod
    @override
    def install_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to install a Python runtime via pymanager."""
        return ['py', 'install', package.name]

    @staticmethod
    @override
    def upgrade_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a Python runtime via pymanager."""
        return ['py', 'install', '--update', package.name]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs a Python runtime using pymanager.

        Args:
            params: Installation parameters containing the Python version tag (e.g., "3.12", "3.14t")

        Returns:
            The installed package, or None if installation failed
        """
        logger = logging.getLogger('porringer.pim.install')

        # The package name is the Python version tag (e.g., "3.12", "3.14", "3.14t")
        tag = params.package.name

        args = ['py', 'install', tag]
        if params.dry:
            args.append('--dry-run')
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=tag)

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error("Python Install Manager (py) not found. Install it via winget: 'winget install 9NQ7512CXL7T'")
            return None
        except Exception as e:
            logger.error(f'Failed to install Python {tag}: {e}')
            return None

        # Try to get the actual installed version
        version = self.__class__._get_runtime_version(tag)
        return Package(name=params.package.name, version=version or tag)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches for an available Python runtime online.

        Args:
            package: The package reference to search for (e.g., "3.12")

        Returns:
            The package if found, or None if it doesn't exist
        """
        logger = logging.getLogger('porringer.pim.search')
        tag = package.name

        try:
            # Search online for available runtimes matching the tag
            result = subprocess.run(
                ['py', 'list', '--online', '-f', 'json', tag],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f'Search for Python {tag} returned no results')
                return None

            # Parse JSON output
            runtimes = json.loads(result.stdout) if result.stdout.strip() else []
            if runtimes:
                # Return the first matching runtime
                runtime = runtimes[0]
                version = runtime.get('tag', tag)
                return Package(name=package.name, version=version)

        except FileNotFoundError:
            logger.error('Python Install Manager (py) not found')
        except json.JSONDecodeError:
            logger.warning(f'Could not parse search results for {tag}')
        except Exception as e:
            logger.error(f'Failed to search for Python {tag}: {e}')

        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls Python runtimes.

        Args:
            params: Uninstall parameters containing the list of version tags to remove

        Returns:
            A list of uninstalled packages, with None for any that failed
        """
        logger = logging.getLogger('porringer.pim.uninstall')
        results: list[Package | None] = []

        for pkg in params.packages:
            tag = pkg.name
            # Use -y to skip confirmation prompt
            args = ['py', 'uninstall', '-y', tag]

            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=pkg.name, version=tag))
                continue

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=tag))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('Python Install Manager (py) not found')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall Python {tag}: {e}')
                results.append(None)

        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades a Python runtime to its latest patch version.

        Args:
            params: Upgrade parameters containing the version tag to upgrade

        Returns:
            The upgraded package, or None if the upgrade failed
        """
        logger = logging.getLogger('porringer.pim.upgrade')
        pkg = params.package
        tag = pkg.name
        # Use --update flag to upgrade existing installs
        args = ['py', 'install', '--update', tag]

        if params.dry:
            args.append('--dry-run')
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=tag)

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('Python Install Manager (py) not found')
            return None
        except Exception as e:
            logger.error(f'Failed to upgrade Python {tag}: {e}')
            return None

        version = self.__class__._get_runtime_version(tag)
        return Package(name=pkg.name, version=version or tag)

    @override
    def packages(self) -> list[Package]:
        """Lists all installed Python runtimes.

        Returns:
            A list of installed Python runtime packages
        """
        logger = logging.getLogger('porringer.pim.packages')
        packages: list[Package] = []

        try:
            # List installed runtimes in JSON format, only those managed by pymanager
            result = subprocess.run(
                ['py', 'list', '--only-managed', '-f', 'json'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed Python runtimes')
                return packages

            # Parse JSON output - format is {"versions": [...]}
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            runtimes = data.get('versions', []) if isinstance(data, dict) else []
            for runtime in runtimes:
                tag = runtime.get('tag', 'unknown')
                version = runtime.get('sort-version') or tag
                # Use tag as the package name since that's how users identify runtimes
                packages.append(
                    Package(
                        name=tag,
                        version=version,
                    )
                )

        except FileNotFoundError:
            logger.error('Python Install Manager (py) not found')
        except json.JSONDecodeError:
            logger.warning('Could not parse installed runtimes list')
        except Exception as e:
            logger.error(f'Failed to list Python runtimes: {e}')

        return packages

    @staticmethod
    def _get_runtime_version(tag: str) -> str | None:
        """Gets the actual version string for an installed runtime.

        Args:
            tag: The Python version tag (e.g., "3.12")

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['py', 'list', '--only-managed', '-f', 'json', tag],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                runtimes = data.get('versions', []) if isinstance(data, dict) else []
                if runtimes:
                    return runtimes[0].get('sort-version') or runtimes[0].get('tag')
        except Exception:
            pass

        return None
