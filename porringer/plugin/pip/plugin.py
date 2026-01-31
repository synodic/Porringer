"""Plugin implementation"""

import logging
import subprocess
from importlib.metadata import distributions
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    ProviderRequirement,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName

# Capability identifier for Python runtime providers
PYTHON_RUNTIME_CAPABILITY = 'python-runtime'


class PipEnvironment(Environment):
    """Represents a Python environment managed by pip.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using pip
    as the backend package manager.

    This plugin can optionally use a Python runtime provider (like pim) for managing
    the underlying Python installation.
    """

    @staticmethod
    @override
    def requires_providers() -> list[ProviderRequirement]:
        """Declares that pip can optionally use a Python runtime provider.

        The provider is optional - pip can also work with system-installed Python.
        Returns a list of platform-specific providers that the builder can select from:
        - pim: Python Install Manager (Windows)
        - brew: Homebrew (macOS)
        - apt: APT package manager (Linux)

        NOTE: Currently defaults to using the latest available Python from the provider.
        Future versions may support configuration for selecting specific versions.

        Returns:
            A list of provider requirements for each supported platform
        """
        return [
            # Windows: Python Install Manager
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='pim',
            ),
            # macOS: Homebrew
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='brew',
            ),
            # Linux: APT
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='apt',
            ),
        ]

    @staticmethod
    @override
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command to install a package via pip."""
        return ['pip', 'install', str(package)]

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
        return [
            Package(name=PackageName(dist.metadata['Name']), version=dist.version)
            for dist in distributions()
            if dist.metadata['Name'] is not None
        ]
