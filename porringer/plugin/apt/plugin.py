"""Plugin implementation for APT (Advanced Package Tool) package manager."""

import logging
import subprocess
import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
)
from porringer.core.schema import Ecosystem, Package, PackageRef


class AptEnvironment(Environment):
    """Represents a Debian/Ubuntu Linux environment managed by APT.

    Provides methods to install, search, uninstall, upgrade, and list packages using
    APT as the backend package manager.

    This plugin is available on Debian-based Linux distributions (Debian, Ubuntu, etc.).

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions via packages like python3.12.

    Note:
        APT operations (install, remove, upgrade) require root privileges.
        Run with sudo: `sudo porringer ...`

    CLI Reference:
        - dpkg -l  - List installed packages
        - apt install <package>  - Install a package
        - apt remove <package>  - Remove a package
        - apt upgrade <package>  - Upgrade a package
        - apt-cache show <package>  - Get package info
        - apt-cache search <text>  - Search for packages
        - apt-cache policy <package>  - Get version/installation status
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """APT belongs to the `system` ecosystem."""
        return Ecosystem('system')

    @staticmethod
    @override
    def is_supported() -> bool:
        """Supported on Linux only."""
        return sys.platform not in {'win32', 'darwin'}

    @classmethod
    @override
    def tool_name(cls) -> str:
        """APT wraps the `apt` CLI."""
        return 'apt'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via apt."""
        # apt uses name=version for exact pinning
        if package.constraint:
            return ['apt', 'install', f'{package.name}={package.constraint}']
        return ['apt', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via apt."""
        if package.constraint:
            return [
                'apt',
                'install',
                '--only-upgrade',
                f'{package.name}={package.constraint}',
            ]
        return ['apt', 'install', '--only-upgrade', package.name]

    @override
    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs a package using APT.

        Overrides the base to use `-y` auto-confirm and resolve
        the installed version afterward.
        """
        if params.progress_callback is None:
            return await super().async_install(params)

        logger = logging.getLogger('porringer.apt.install')
        package = params.package.name

        if params.dry:
            args = ['apt', 'install', '--simulate', package]
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=None)

        result = await self._stream_command(
            args=['apt', 'install', '-y', package],
            params=params,
            phase='installing',
            verb='install',
        )
        if result is not None:
            version = self.__class__._get_package_version(package)
            return Package(name=result.name, version=version)
        return None

    @override
    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades a package using APT.

        Overrides the base to use `-y --only-upgrade` and resolve
        the installed version afterward.
        """
        if params.progress_callback is None:
            return await super().async_upgrade(params)

        logger = logging.getLogger('porringer.apt.upgrade')
        package = params.package.name

        if params.dry:
            args = ['apt', 'install', '--simulate', '--only-upgrade', package]
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=None)

        result = await self._stream_command(
            args=['apt', 'install', '-y', '--only-upgrade', package],
            params=params,
            phase='upgrading',
            verb='upgrade',
        )
        if result is not None:
            version = self.__class__._get_package_version(package)
            return Package(name=result.name, version=version)
        return None

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Lists all installed packages via dpkg.

        apt manages system packages globally; *project_path* is accepted
        for interface compatibility but has no effect.

        Args:
            project_path: Unused.  apt is inherently global.

        Returns:
            A list of installed packages
        """
        logger = logging.getLogger('porringer.apt.packages')
        packages: list[Package] = []

        try:
            # Use dpkg-query for more structured output
            # Format: package-name\tversion
            result = subprocess.run(
                ['dpkg-query', '-W', '-f', '${Package}\t${Version}\n'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed packages')
                return packages

            # Parse the tab-separated output
            for line in result.stdout.strip().split('\n'):
                if '\t' in line:
                    parts = line.split('\t', 1)
                    name = parts[0].strip()
                    version = parts[1].strip() if len(parts) > 1 else 'unknown'
                    if name:
                        packages.append(
                            Package(
                                name=name,
                                version=version,
                            )
                        )

        except FileNotFoundError:
            logger.error('dpkg-query not found')
        except Exception as e:
            logger.error(f'Failed to list packages: {e}')

        return packages

    @staticmethod
    def _get_package_version(package: str) -> str | None:
        """Gets the installed version for a package.

        Args:
            package: The package name

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['dpkg-query', '-W', '-f', '${Version}', package],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        return None
