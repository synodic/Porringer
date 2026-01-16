"""Plugin implementation"""

import json
import logging
import subprocess
from pathlib import Path
from typing import override

from platformdirs import user_data_dir

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName


class PipxEnvironment(Environment):
    """Represents a Python environment managed by pipx.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using
    pipx as the backend package manager.
    """

    @override
    def install(self, params: InstallParameters) -> Package | None:
        """Installs the given package identified by its name using pipx."""
        logger = logging.getLogger('porringer.pipx.install')
        args = ['pipx', 'install', str(params.name)]
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.name, version='unknown')
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
        """Uninstalls the given list of packages using pipx."""
        logger = logging.getLogger('porringer.pipx.uninstall')
        results: list[Package | None] = []
        for name in params.names:
            args = ['pipx', 'uninstall', str(name)]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=name, version='unknown'))
                continue
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
        """Upgrades the given list of packages using pipx."""
        logger = logging.getLogger('porringer.pipx.upgrade')
        results: list[Package | None] = []
        for name in params.names:
            args = ['pipx', 'upgrade', str(name)]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=name, version='unknown'))
                continue
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
        packages: list[Package] = []
        pipx_home = Path(user_data_dir('pipx', 'pypa')) / 'venvs'

        if not pipx_home.exists():
            return packages

        for venv_dir in pipx_home.iterdir():
            metadata_file = venv_dir / 'pipx_metadata.json'
            if metadata_file.exists():
                try:
                    metadata = json.loads(metadata_file.read_text())
                    main_package = metadata.get('main_package', {})
                    name = main_package.get('package')
                    version = main_package.get('package_version', 'unknown')
                    if name:
                        packages.append(Package(name=PackageName(name), version=version))
                except json.JSONDecodeError, KeyError:
                    continue

        return packages
