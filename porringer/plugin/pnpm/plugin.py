"""Plugin implementation for pnpm environment."""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Ecosystem, Package, PackageRef


class PnpmEnvironment(Environment):
    """Represents a Node.js environment managed by pnpm.

    Provides methods to install, search, uninstall, upgrade, and list
    Node.js packages using pnpm as the backend package manager.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Pnpm belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pnpm wraps the `pnpm` CLI."""
        return 'pnpm'

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to install a package via pnpm."""
        if package.constraint:
            return ['pnpm', 'add', '-g', f'{package.name}@{package.constraint}']
        return ['pnpm', 'add', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to upgrade a package via pnpm."""
        if package.constraint:
            return ['pnpm', 'update', '-g', f'{package.name}@{package.constraint}']
        return ['pnpm', 'update', '-g', '--latest', package.name]

    @override
    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying the npm registry.

        pnpm uses the same npm registry; delegates to the shared
        ``_check_npm_registry`` helper.

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
        """Gathers globally installed pnpm packages.

        Uses `pnpm list -g --json --depth=0` to list top-level global
        packages and parses the JSON output.  *project_path* is
        accepted for interface compatibility but ignored for now.

        Args:
            project_path: Unused.

        Returns:
            A list of installed packages.
        """
        entries = self._run_json_command(['pnpm', 'list', '-g', '--json', '--depth=0'])
        if entries is None:
            return []
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
