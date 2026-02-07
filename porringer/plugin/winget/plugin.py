"""Plugin implementation"""

import logging
import shutil
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class WingetEnvironment(Environment):
    """Represents a Windows environment managed by winget.

    Provides methods to install, search, uninstall, upgrade, and list packages using winget
    as the backend package manager.
    """

    @staticmethod
    @override
    def is_available() -> bool:
        """Checks if winget is available on the system.

        Returns:
            True if winget is found on PATH, False otherwise.
        """
        return shutil.which('winget') is not None

    @staticmethod
    @override
    def install_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via winget."""
        cmd = ['winget', 'install', '--id', package.name]
        if package.constraint:
            cmd.extend(['--version', package.constraint])
        return cmd

    @staticmethod
    @override
    def upgrade_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via winget."""
        cmd = ['winget', 'upgrade', '--id', package.name]
        if package.constraint:
            cmd.extend(['--version', package.constraint])
        return cmd

    @override
    def install(self, params: PackageParameters) -> Package | None:
        logger = logging.getLogger('porringer.winget.install')
        pkg = params.package
        args = [
            'winget',
            'install',
            '--id',
            pkg.name,
            '--accept-source-agreements',
            '--accept-package-agreements',
            '-e',
        ]
        if pkg.constraint:
            args.extend(['--version', pkg.constraint])
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('winget not found. Install it from https://github.com/microsoft/winget-cli')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {pkg.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            package: The package reference to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        logger = logging.getLogger('porringer.winget.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = [
                'winget',
                'uninstall',
                '--id',
                pkg.name,
                '--accept-source-agreements',
                '--accept-package-agreements',
                '-e',
            ]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=pkg.name, version=None))
                continue
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('winget not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        logger = logging.getLogger('porringer.winget.upgrade')
        pkg = params.package
        args = [
            'winget',
            'upgrade',
            '--id',
            pkg.name,
            '--accept-source-agreements',
            '--accept-package-agreements',
            '-e',
        ]
        if pkg.constraint:
            args.extend(['--version', pkg.constraint])
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('winget not found')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        return []
