"""Plugin implementation for Bun environment."""

import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class BunEnvironment(Environment):
    """Represents a Node.js environment managed by Bun.

    Provides methods to install, search, uninstall, upgrade, and list
    Node.js packages using Bun as the backend package manager.  Bun is
    npm-compatible: it reads `package.json`, uses `node_modules`,
    and installs from the npm registry.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Bun belongs to the `node` ecosystem."""
        return 'node'

    @staticmethod
    @override
    def default_priority() -> int:
        """Bun is a lower-priority Node installer (priority 30)."""
        return 30

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Bun wraps the `bun` CLI."""
        return 'bun'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via Bun."""
        if package.constraint:
            return ['bun', 'add', '-g', f'{package.name}@{package.constraint}']
        return ['bun', 'add', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via Bun."""
        # Bun has no per-package global update; re-add at latest
        return ['bun', 'add', '-g', f'{package.name}@latest']

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package globally using Bun."""
        logger = logging.getLogger('porringer.bun.install')
        pkg = params.package
        args = list(self.install_command(pkg))
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('bun not found. Install it from https://bun.sh')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Bun does not provide a search CLI; returns `None`."""
        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using Bun."""
        logger = logging.getLogger('porringer.bun.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['bun', 'remove', '-g', pkg.name]
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
                logger.error('bun not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given package using Bun."""
        logger = logging.getLogger('porringer.bun.upgrade')
        pkg = params.package
        args = list(self.upgrade_command(pkg))
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('bun not found')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def packages(self) -> list[Package]:
        """Gathers globally installed Bun packages.

        Bun does not provide a JSON list for globally installed packages;
        returns an empty list.

        Returns:
            An empty list.
        """
        return []
