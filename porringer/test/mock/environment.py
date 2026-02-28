"""Mock environment data"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Package, PackageRef


class MockEnvironment(Environment):
    """Mocked environment plugin"""

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to install a package."""
        return ['mock', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to upgrade a package."""
        return ['mock', 'upgrade', str(package)]

    @override
    def uninstall_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to uninstall a package."""
        return ['mock', 'uninstall', package.name]

    @override
    async def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages in the given environment

        Args:
            project_path: Unused in mock.

        Returns:
            A list of packages
        """
        return []

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Mock update check — always returns empty."""
        del params
        return []
