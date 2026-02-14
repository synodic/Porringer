"""Plugin implementation for pnpm environment."""

import json
import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
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
