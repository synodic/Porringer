"""Plugin implementation"""

import logging
import shutil
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName


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
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command to install a package via winget."""
        return ['winget', 'install', '--id', str(package)]

    @override
    def install(self, params: InstallParameters) -> Package | None:
        logger = logging.getLogger('porringer.winget.install')
        args = [
            'winget',
            'install',
            '--id',
            str(params.name),
            '--accept-source-agreements',
            '--accept-package-agreements',
            '-e',
        ]
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.name, version=None)
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
            logger.error(f'Failed to install {params.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {params.name}: {e}')
            return None
        return Package(name=params.name, version=None)

    @override
    def search(self, name: PackageName) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            name: The package name to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        logger = logging.getLogger('porringer.winget.uninstall')
        results: list[Package | None] = []
        for name in params.names:
            args = [
                'winget',
                'uninstall',
                '--id',
                str(name),
                '--accept-source-agreements',
                '--accept-package-agreements',
                '-e',
            ]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=name, version=None))
                continue
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('winget not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        logger = logging.getLogger('porringer.winget.upgrade')
        results: list[Package | None] = []
        for name in params.names:
            args = [
                'winget',
                'upgrade',
                '--id',
                str(name),
                '--accept-source-agreements',
                '--accept-package-agreements',
                '-e',
            ]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=name, version=None))
                continue
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('winget not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to upgrade {name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to upgrade {name}: {e}')
                results.append(None)
        return results

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        return []
