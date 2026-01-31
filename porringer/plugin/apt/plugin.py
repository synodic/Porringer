"""Plugin implementation for APT (Advanced Package Tool) package manager."""

import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    ProviderCapability,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName

# Capability identifier for Python runtime providers
PYTHON_RUNTIME_CAPABILITY = 'python-runtime'


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
    def provides() -> list[ProviderCapability]:
        """Declares that this plugin provides Python runtime management.

        APT can install versioned Python packages like python3.12.

        Returns:
            A list containing the python-runtime capability
        """
        return [
            ProviderCapability(
                capability=PYTHON_RUNTIME_CAPABILITY,
                description='Manages Python runtime versions via APT packages',
            ),
        ]

    @staticmethod
    @override
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command to install a package via apt."""
        return ['apt', 'install', str(package)]

    @override
    def install(self, params: InstallParameters) -> Package | None:
        """Installs a package using APT.

        Note: Requires root privileges. Run with sudo.

        Args:
            params: Installation parameters containing the package name

        Returns:
            The installed package, or None if installation failed
        """
        logger = logging.getLogger('porringer.apt.install')

        package = str(params.name)

        if params.dry:
            # Use --simulate for dry run
            args = ['apt', 'install', '--simulate', package]
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
            except Exception:
                pass
            return Package(name=params.name, version='unknown')

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
        return Package(name=params.name, version=version or 'unknown')

    @override
    def search(self, name: PackageName) -> Package | None:
        """Searches for a package in APT repositories.

        Args:
            name: The package name to search for

        Returns:
            The package if found, or None if it doesn't exist
        """
        logger = logging.getLogger('porringer.apt.search')
        package = str(name)

        try:
            # Use apt-cache policy to check if package exists and get version info
            result = subprocess.run(
                ['apt-cache', 'policy', package],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f'Package {package} not found')
                return None

            # Parse the output to get the candidate version
            # Output format includes "Candidate: <version>" line
            output = result.stdout
            if 'Candidate:' in output:
                for line in output.split('\n'):
                    if 'Candidate:' in line:
                        version = line.split('Candidate:')[1].strip()
                        if version and version != '(none)':
                            return Package(name=name, version=version)

            logger.warning(f'No candidate version found for {package}')

        except FileNotFoundError:
            logger.error('apt-cache not found')
        except Exception as e:
            logger.error(f'Failed to search for {package}: {e}')

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

        for name in params.names:
            package = str(name)

            if params.dry:
                args = ['apt', 'remove', '--simulate', package]
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                try:
                    result = subprocess.run(args, capture_output=True, text=True, check=False)
                    logger.info(result.stdout)
                except Exception:
                    pass
                results.append(Package(name=name, version='unknown'))
                continue

            # Use -y to auto-confirm
            args = ['apt', 'remove', '-y', package]

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version='unknown'))
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
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        """Upgrades packages using APT.

        Note: Requires root privileges. Run with sudo.

        Args:
            params: Upgrade parameters containing the list of package names

        Returns:
            A list of upgraded packages, with None for any that failed
        """
        logger = logging.getLogger('porringer.apt.upgrade')
        results: list[Package | None] = []

        for name in params.names:
            package = str(name)

            if params.dry:
                args = ['apt', 'install', '--simulate', '--only-upgrade', package]
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                try:
                    result = subprocess.run(args, capture_output=True, text=True, check=False)
                    logger.info(result.stdout)
                except Exception:
                    pass
                results.append(Package(name=name, version='unknown'))
                continue

            # Use install --only-upgrade to upgrade a specific package
            args = ['apt', 'install', '-y', '--only-upgrade', package]

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    version = self.__class__._get_package_version(package)
                    results.append(Package(name=name, version=version or 'unknown'))
                else:
                    logger.error(result.stderr)
                    if 'Permission denied' in result.stderr or 'are you root?' in result.stderr:
                        logger.error('APT requires root privileges. Run with sudo.')
                    results.append(None)
            except FileNotFoundError:
                logger.error('APT not found')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to upgrade {package}: {e}')
                results.append(None)

        return results

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
                                name=PackageName(name),
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
