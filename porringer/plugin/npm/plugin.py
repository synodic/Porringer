"""Plugin implementation"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Ecosystem, Package, PackageRef


class NpmEnvironment(Environment):
    """Represents a Node.js environment managed by npm.

    Provides methods to install, search, uninstall, upgrade, and list Node.js packages using npm
    as the backend package manager.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Npm belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm wraps the `npm` CLI."""
        return 'npm'

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to install a package via npm."""
        # npm uses name@constraint syntax for version pinning
        if package.constraint:
            return ['npm', 'install', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'install', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to upgrade a package via npm."""
        if package.constraint:
            return ['npm', 'update', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'update', '-g', package.name]

    @override
    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying the npm registry.

        Delegates to the shared ``_check_npm_registry`` helper.

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        return self._check_npm_registry(
            params.packages,
            include_prereleases=params.include_prereleases,
        )

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed npm packages.

        Uses `npm ls -g --json --depth=0` to list top-level global
        packages and parses the JSON output.  *project_path* is
        accepted for interface compatibility but ignored for now.

        Args:
            project_path: Unused.

        Returns:
            A list of installed packages.
        """
        data = self._run_json_command(['npm', 'ls', '-g', '--json', '--depth=0'])
        if data is None or not isinstance(data, dict):
            return []
        deps = data.get('dependencies', {})
        return [
            Package(name=name, version=info.get('version')) for name, info in deps.items() if isinstance(info, dict)
        ]
