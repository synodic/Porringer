"""Plugin implementation for APT (Advanced Package Tool) package manager."""

import logging
import subprocess
import sys
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class AptEnvironment(Environment):
    """Represents a Debian/Ubuntu Linux environment managed by APT.

    Provides methods to install, search, uninstall, upgrade, and list packages using
    APT as the backend package manager.

    This plugin is available on Debian-based Linux distributions (Debian, Ubuntu, etc.).

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions via packages like python3.12.

    Note:
        APT operations (install, remove, upgrade) require root privileges.
        Run with sudo: ``sudo porringer ...``

    CLI Reference:
        - dpkg -l  - List installed packages
        - apt install <package>  - Install a package
        - apt remove <package>  - Remove a package
        - apt upgrade <package>  - Upgrade a package
        - apt-cache show <package>  - Get package info
        - apt-cache search <text>  - Search for packages
        - apt-cache policy <package>  - Get version/installation status
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """APT belongs to the ``system`` ecosystem."""
        return 'system'

    @staticmethod
    @override
    def default_priority() -> int:
        """Preferred on Linux (10), unavailable elsewhere (999)."""
        if sys.platform in ('win32', 'darwin'):
            return 999
        return 10

    @classmethod
    @override
    def tool_name(cls) -> str:
        """APT wraps the ``apt`` CLI."""
        return 'apt'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via apt."""
        # apt uses name=version for exact pinning
        if package.constraint:
            return ['apt', 'install', f'{package.name}={package.constraint}']
        return ['apt', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via apt."""
        if package.constraint:
            return ['apt', 'install', '--only-upgrade', f'{package.name}={package.constraint}']
        return ['apt', 'install', '--only-upgrade', package.name]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs a package using APT.

        Note: Requires root privileges. Run with sudo.

        Args:
            params: Installation parameters containing the package name

        Returns:
            The installed package, or None if installation failed
        """
        logger = logging.getLogger('porringer.apt.install')

        package = params.package.name

        if params.dry:
            # Use --simulate for dry run
            args = ['apt', 'install', '--simulate', package]
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
            except Exception:
                pass
            return Package(name=params.package.name, version=None)

        # Use -y to auto-confirm
        args = ['apt', 'install', '-y', package]

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                if 'Permission denied' in result.stderr or 'are you root?' in result.stderr:
                    logger.error('APT requires root privileges. Run with sudo.')
                return None
        except FileNotFoundError:
            logger.error('APT not found. This plugin requires a Debian-based Linux distribution.')
            return None
        except Exception as e:
            logger.error(f'Failed to install {package}: {e}')
            return None

        # Try to get the installed version
        version = self.__class__._get_package_version(package)
        return Package(name=params.package.name, version=version)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches for a package in APT repositories.

        Args:
            package: The package reference to search for

        Returns:
            The package if found, or None if it doesn't exist
        """
        logger = logging.getLogger('porringer.apt.search')
        pkg_name = package.name

        try:
            # Use apt-cache policy to check if package exists and get version info
            result = subprocess.run(
                ['apt-cache', 'policy', pkg_name],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f'Package {pkg_name} not found')
                return None

            # Parse the output to get the candidate version
            # Output format includes "Candidate: <version>" line
            output = result.stdout
            if 'Candidate:' in output:
                for line in output.split('\n'):
                    if 'Candidate:' in line:
                        version = line.split('Candidate:')[1].strip()
                        if version and version != '(none)':
                            return Package(name=package.name, version=version)

            logger.warning(f'No candidate version found for {pkg_name}')

        except FileNotFoundError:
            logger.error('apt-cache not found')
        except Exception as e:
            logger.error(f'Failed to search for {pkg_name}: {e}')

        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls packages using APT.

        Note: Requires root privileges. Run with sudo.

        Args:
            params: Uninstall parameters containing the list of package names

        Returns:
            A list of uninstalled packages, with None for any that failed
        """
        logger = logging.getLogger('porringer.apt.uninstall')
        results: list[Package | None] = []

        for pkg in params.packages:
            package = pkg.name

            if params.dry:
                args = ['apt', 'remove', '--simulate', package]
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                try:
                    result = subprocess.run(args, capture_output=True, text=True, check=False)
                    logger.info(result.stdout)
                except Exception:
                    pass
                results.append(Package(name=pkg.name, version=None))
                continue

            # Use -y to auto-confirm
            args = ['apt', 'remove', '-y', package]

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    if 'Permission denied' in result.stderr or 'are you root?' in result.stderr:
                        logger.error('APT requires root privileges. Run with sudo.')
                    results.append(None)
            except FileNotFoundError:
                logger.error('APT not found')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {package}: {e}')
                results.append(None)

        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades a package using APT.

        Note: Requires root privileges. Run with sudo.

        Args:
            params: Upgrade parameters containing the package name

        Returns:
            The upgraded package, or None if the upgrade failed
        """
        logger = logging.getLogger('porringer.apt.upgrade')
        pkg = params.package
        package = pkg.name

        if params.dry:
            args = ['apt', 'install', '--simulate', '--only-upgrade', package]
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
            except Exception:
                pass
            return Package(name=pkg.name, version=None)

        # Use install --only-upgrade to upgrade a specific package
        args = ['apt', 'install', '-y', '--only-upgrade', package]

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                if 'Permission denied' in result.stderr or 'are you root?' in result.stderr:
                    logger.error('APT requires root privileges. Run with sudo.')
                return None
        except FileNotFoundError:
            logger.error('APT not found')
            return None
        except Exception as e:
            logger.error(f'Failed to upgrade {package}: {e}')
            return None

        version = self.__class__._get_package_version(package)
        return Package(name=pkg.name, version=version)

    @override
    def packages(self) -> list[Package]:
        """Lists all installed packages via dpkg.

        Returns:
            A list of installed packages
        """
        logger = logging.getLogger('porringer.apt.packages')
        packages: list[Package] = []

        try:
            # Use dpkg-query for more structured output
            # Format: package-name\tversion
            result = subprocess.run(
                ['dpkg-query', '-W', '-f', '${Package}\t${Version}\n'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed packages')
                return packages

            # Parse the tab-separated output
            for line in result.stdout.strip().split('\n'):
                if '\t' in line:
                    parts = line.split('\t', 1)
                    name = parts[0].strip()
                    version = parts[1].strip() if len(parts) > 1 else 'unknown'
                    if name:
                        packages.append(
                            Package(
                                name=name,
                                version=version,
                            )
                        )

        except FileNotFoundError:
            logger.error('dpkg-query not found')
        except Exception as e:
            logger.error(f'Failed to list packages: {e}')

        return packages

    @staticmethod
    def _get_package_version(package: str) -> str | None:
        """Gets the installed version for a package.

        Args:
            package: The package name

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['dpkg-query', '-W', '-f', '${Version}', package],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        return None
