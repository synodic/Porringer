"""Plugin implementation for Bun environment."""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
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
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed Bun packages.

        Bun does not provide a JSON list for globally installed packages;
        returns an empty list.  *project_path* is accepted for interface
        compatibility.

        Args:
            project_path: Unused.

        Returns:
            An empty list.
        """
        return []
