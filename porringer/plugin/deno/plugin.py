"""Plugin implementation for Deno environment."""

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


class DenoEnvironment(Environment):
    """Represents a Deno environment.

    Provides methods to install, uninstall, and upgrade global scripts
    using `deno install -g`.  Deno supports both npm (`npm:`) and
    JSR (`jsr:`) package sources.  By default, bare package names are
    prefixed with `npm:` unless they already carry a source prefix.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Deno belongs to the `deno` ecosystem."""
        return 'deno'

    @staticmethod
    @override
    def default_priority() -> int:
        """Deno is the sole Deno installer (priority 10)."""
        return 10

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Deno wraps the `deno` CLI."""
        return 'deno'

    @staticmethod
    def _deno_specifier(package: PackageRef) -> str:
        """Build a Deno-compatible package specifier.

        Prefixes the package name with `npm:` when no source prefix
        (`npm:`, `jsr:`, `https:`) is present.  Appends the
        constraint with `@` separator if provided.
        """
        name = package.name
        if not any(name.startswith(p) for p in ('npm:', 'jsr:', 'https:', 'http:')):
            name = f'npm:{name}'
        if package.constraint:
            return f'{name}@{package.constraint}'
        return name

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a global script via Deno."""
        return ['deno', 'install', '-g', self._deno_specifier(package)]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a global script via Deno."""
        return ['deno', 'install', '-g', '--force', self._deno_specifier(package)]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package as a global Deno script."""
        logger = logging.getLogger('porringer.deno.install')
        pkg = params.package
        args = list(self.install_command(pkg))
        if params.dry:
            # Deno has no --dry-run; log the command instead
            logger.info('Dry run: %s', ' '.join(args))
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('deno not found. Install it from https://deno.land')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Deno does not provide a search CLI; returns `None`."""
        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of global Deno scripts."""
        logger = logging.getLogger('porringer.deno.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['deno', 'uninstall', '-g', pkg.name]
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
                logger.error('deno not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given global Deno script."""
        logger = logging.getLogger('porringer.deno.upgrade')
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
            logger.error('deno not found')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed Deno scripts.

        Deno does not provide a structured list of globally installed
        scripts; returns an empty list.  *project_path* is accepted
        for interface compatibility.

        Args:
            project_path: Unused.

        Returns:
            An empty list.
        """
        return []
