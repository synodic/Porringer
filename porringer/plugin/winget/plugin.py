"""Plugin implementation"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
)
from porringer.core.schema import Package, PackageRef


class WingetEnvironment(Environment):
    """Represents a Windows environment managed by winget.

    Provides methods to install, search, uninstall, upgrade, and list packages using winget
    as the backend package manager.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Winget belongs to the `system` ecosystem."""
        return 'system'

    @staticmethod
    @override
    def default_priority() -> int:
        """Preferred on Windows (10), unavailable elsewhere (999)."""
        if sys.platform == 'win32':
            return 10
        return 999

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Winget wraps the `winget` CLI."""
        return 'winget'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via winget."""
        cmd = ['winget', 'install', '--id', package.name]
        if package.constraint:
            cmd.extend(['--version', package.constraint])
        return cmd

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via winget."""
        cmd = ['winget', 'upgrade', '--id', package.name]
        if package.constraint:
            cmd.extend(['--version', package.constraint])
        return cmd

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages in the given environment.

        winget manages system packages globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  winget is inherently global.

        Returns:
            A list of packages
        """
        return []

    @override
    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package using winget.

        Overrides the base to add `--accept-source-agreements` and
        other winget-specific flags.
        """
        if params.progress_callback is None:
            return await super().async_install(params)

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
            logging.getLogger('porringer.winget.install').info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)

        return await self._stream_command(
            args=args,
            params=params,
            phase='installing',
            verb='install',
        )

    @override
    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades the given package using winget.

        Overrides the base to add `--accept-source-agreements` and
        other winget-specific flags.
        """
        if params.progress_callback is None:
            return await super().async_upgrade(params)

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
            logging.getLogger('porringer.winget.upgrade').info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)

        return await self._stream_command(
            args=args,
            params=params,
            phase='upgrading',
            verb='upgrade',
        )
