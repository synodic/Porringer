"""Plugin implementation"""

import json
import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Package, PackageRef


class NpmEnvironment(Environment):
    """Represents a Node.js environment managed by npm.

    Provides methods to install, search, uninstall, upgrade, and list Node.js packages using npm
    as the backend package manager.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Npm belongs to the `node` ecosystem."""
        return 'node'

    @staticmethod
    @override
    def default_priority() -> int:
        """Npm is a standard Node installer (priority 20)."""
        return 20

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm wraps the `npm` CLI."""
        return 'npm'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via npm."""
        # npm uses name@constraint syntax for version pinning
        if package.constraint:
            return ['npm', 'install', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'install', '-g', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via npm."""
        if package.constraint:
            return ['npm', 'update', '-g', f'{package.name}@{package.constraint}']
        return ['npm', 'update', '-g', package.name]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed npm packages.

        Uses ``npm ls -g --json --depth=0`` to list top-level global
        packages and parses the JSON output.  *project_path* is
        accepted for interface compatibility but ignored for now.

        Args:
            project_path: Unused.

        Returns:
            A list of installed packages.
        """
        logger = logging.getLogger('porringer.npm.packages')
        try:
            result = subprocess.run(
                ['npm', 'ls', '-g', '--json', '--depth=0'],
                capture_output=True,
                text=True,
                check=False,
            )
            data = json.loads(result.stdout)
            deps = data.get('dependencies', {})
            return [
                Package(name=name, version=info.get('version')) for name, info in deps.items() if isinstance(info, dict)
            ]
        except FileNotFoundError:
            logger.error('npm not found on PATH')
        except (json.JSONDecodeError, subprocess.SubprocessError) as e:
            logger.error('Failed to list npm packages: %s', e)
        return []
