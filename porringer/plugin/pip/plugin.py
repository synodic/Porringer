"""Plugin implementation"""

import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName


class PipEnvironment(Environment):
    """Represents a Python environment managed by pip.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using pip
    as the backend package manager.
    """

    @override
    def install(self, params: InstallParameters) -> Package | None:
        """Installs the given package identified by its name using pip."""
        logger = logging.getLogger('porringer.pip.install')
        args = ['python', '-m', 'pip', 'install', str(params.name)]
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except Exception as e:
            logger.error(f'Failed to install {params.name}: {e}')
            return None
        return Package(name=params.name, version='unknown')

    @override
    def search(self, name: PackageName) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            name: The package name to search for

        Returns:
            The package, or None if it doesn't exist
        """

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using pip."""
        logger = logging.getLogger('porringer.pip.uninstall')
        results: list[Package | None] = []
        for name in params.names:
            args = ['python', '-m', 'pip', 'uninstall', '-y', str(name)]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version='unknown'))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        """Upgrades the given list of packages using pip."""
        logger = logging.getLogger('porringer.pip.upgrade')
        results: list[Package | None] = []
        for name in params.names:
            args = ['python', '-m', 'pip', 'install', '--upgrade', str(name)]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version='unknown'))
                else:
                    logger.error(result.stderr)
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
