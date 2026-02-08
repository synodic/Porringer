"""Plugin implementation"""

import json
import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class NpmEnvironment(Environment):
    """Represents a Node.js environment managed by npm.

    Provides methods to install, search, uninstall, upgrade, and list Node.js packages using npm
    as the backend package manager.
    """

    @staticmethod
    @override
    def package_backend() -> str:
        """Npm manages the ``node`` package backend."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm wraps the ``npm`` CLI."""
        return 'npm'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via npm."""
        # npm uses name@constraint syntax for version pinning
        if package.constraint:
            return ['npm', 'install', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'install', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via npm."""
        if package.constraint:
            return ['npm', 'update', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'update', '-g', package.name]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name using npm."""
        logger = logging.getLogger('porringer.npm.install')
        pkg = params.package
        spec = f'{pkg.name}@{pkg.constraint}' if pkg.constraint else pkg.name
        args = ['npm', 'install', '-g', spec]
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('npm not found. Install Node.js from https://nodejs.org')
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
        """Searches the environment's sources for a package.

        npm does not provide a reliable search CLI; returns ``None``
        to indicate that search is not supported.

        Args:
            package: The package reference to search for

        Returns:
            Always ``None``.
        """
        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using npm."""
        logger = logging.getLogger('porringer.npm.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['npm', 'uninstall', '-g', pkg.name]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('npm not found')
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
        """Upgrades the given package using npm."""
        logger = logging.getLogger('porringer.npm.upgrade')
        pkg = params.package
        spec = f'{pkg.name}@{pkg.constraint}' if pkg.constraint else pkg.name
        args = ['npm', 'update', '-g', spec]
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('npm not found')
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
        """Gathers globally installed npm packages.

        Uses ``npm ls -g --json --depth=0`` to list top-level global
        packages and parses the JSON output.

        Returns:
            A list of installed packages.
        """
        logger = logging.getLogger('porringer.npm.packages')
        try:
            result = subprocess.run(
                ['npm', 'ls', '-g', '--json', '--depth=0'],
                capture_output=True,
                text=True,
                check=False,
            )
            data = json.loads(result.stdout)
            deps = data.get('dependencies', {})
            return [
                Package(name=name, version=info.get('version')) for name, info in deps.items() if isinstance(info, dict)
            ]
        except FileNotFoundError:
            logger.error('npm not found on PATH')
        except (json.JSONDecodeError, subprocess.SubprocessError) as e:
            logger.error('Failed to list npm packages: %s', e)
        return []
