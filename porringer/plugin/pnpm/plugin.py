"""Plugin implementation for pnpm environment."""

import json
import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class PnpmEnvironment(Environment):
    """Represents a Node.js environment managed by pnpm.

    Provides methods to install, search, uninstall, upgrade, and list
    Node.js packages using pnpm as the backend package manager.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Pnpm belongs to the `node` ecosystem."""
        return 'node'

    @staticmethod
    @override
    def default_priority() -> int:
        """Pnpm is the preferred Node installer (priority 10)."""
        return 10

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pnpm wraps the `pnpm` CLI."""
        return 'pnpm'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via pnpm."""
        if package.constraint:
            return ['pnpm', 'add', '-g', f'{package.name}@{package.constraint}']
        return ['pnpm', 'add', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via pnpm."""
        if package.constraint:
            return ['pnpm', 'update', '-g', f'{package.name}@{package.constraint}']
        return ['pnpm', 'update', '-g', '--latest', package.name]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package globally using pnpm."""
        logger = logging.getLogger('porringer.pnpm.install')
        pkg = params.package
        args = list(self.install_command(pkg))
        if params.dry:
            # pnpm has no --dry-run; log the command instead
            logger.info('Dry run: %s', ' '.join(args))
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pnpm not found. Install it from https://pnpm.io/installation')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Pnpm does not provide a search CLI; returns `None`."""
        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using pnpm."""
        logger = logging.getLogger('porringer.pnpm.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['pnpm', 'remove', '-g', pkg.name]
            if params.dry:
                logger.info('Dry run: %s', ' '.join(args))
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
                logger.error('pnpm not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given package using pnpm."""
        logger = logging.getLogger('porringer.pnpm.upgrade')
        pkg = params.package
        args = list(self.upgrade_command(pkg))
        if params.dry:
            logger.info('Dry run: %s', ' '.join(args))
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pnpm not found')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed pnpm packages.

        Uses ``pnpm list -g --json --depth=0`` to list top-level global
        packages and parses the JSON output.  *project_path* is
        accepted for interface compatibility but ignored for now.

        Args:
            project_path: Unused.

        Returns:
            A list of installed packages.
        """
        logger = logging.getLogger('porringer.pnpm.packages')
        try:
            result = subprocess.run(
                ['pnpm', 'list', '-g', '--json', '--depth=0'],
                capture_output=True,
                text=True,
                check=False,
            )
            entries = json.loads(result.stdout)
            # pnpm returns a JSON array; global store is usually the first entry
            if isinstance(entries, list) and entries:
                deps = entries[0].get('dependencies', {})
            elif isinstance(entries, dict):
                deps = entries.get('dependencies', {})
            else:
                return []
            return [
                Package(name=name, version=info.get('version')) for name, info in deps.items() if isinstance(info, dict)
            ]
        except FileNotFoundError:
            logger.error('pnpm not found on PATH')
        except (json.JSONDecodeError, subprocess.SubprocessError) as e:
            logger.error('Failed to list pnpm packages: %s', e)
        return []
