"""Plugin implementation for Homebrew (brew) package manager."""

import json
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


class BrewEnvironment(Environment):
    """Represents a macOS/Linux environment managed by Homebrew (brew).

    Provides methods to install, search, uninstall, upgrade, and list packages using
    Homebrew as the backend package manager.

    This plugin is available on macOS (darwin) and Linux where Homebrew is installed.

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions via formulas like python@3.12.

    CLI Reference:
        - brew list [--formula] [--json]  - List installed formulas
        - brew install <formula>  - Install a formula
        - brew uninstall <formula>  - Uninstall a formula
        - brew upgrade <formula>  - Upgrade a formula
        - brew info <formula> --json=v2  - Get formula info in JSON format
        - brew search <text>  - Search for formulas
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Homebrew belongs to the `system` ecosystem."""
        return 'system'

    @staticmethod
    @override
    def default_priority() -> int:
        """Preferred on macOS (10), fallback on Linux (20), unavailable on Windows (999)."""
        if sys.platform == 'darwin':
            return 10
        if sys.platform == 'win32':
            return 999
        return 20

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Homebrew wraps the `brew` CLI."""
        return 'brew'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via brew."""
        return ['brew', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via brew."""
        return ['brew', 'upgrade', package.name]

    @override
    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs a formula using Homebrew.

        Delegates streaming to the base class, then resolves the
        installed version via `brew info`.
        """
        result = await super().async_install(params)
        if result is not None and result.version is None:
            result = Package(
                name=result.name,
                version=self.__class__._get_formula_version(result.name),
            )
        return result

    @override
    async def async_upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades a formula using Homebrew.

        Delegates streaming to the base class, then resolves the
        installed version via `brew info`.
        """
        result = await super().async_upgrade(params)
        if result is not None and result.version is None:
            result = Package(
                name=result.name,
                version=self.__class__._get_formula_version(result.name),
            )
        return result

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Lists all installed formulas in Homebrew.

        Homebrew manages system packages globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  Homebrew is inherently global.

        Returns:
            A list of installed packages
        """
        logger = logging.getLogger('porringer.brew.packages')
        packages: list[Package] = []

        try:
            # List installed formulas in JSON format
            result = subprocess.run(
                ['brew', 'list', '--formula', '--json'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed formulas')
                return packages

            # The JSON output is a list of formula objects
            # Each has 'name' and 'installed' (list with version info)
            formulas = json.loads(result.stdout) if result.stdout.strip() else []
            for formula in formulas:
                name = formula.get('name', 'unknown')
                # Get the first installed version
                installed = formula.get('installed', [])
                version = installed[0].get('version', 'unknown') if installed else 'unknown'
                packages.append(
                    Package(
                        name=name,
                        version=version,
                    )
                )

        except FileNotFoundError:
            logger.error('Homebrew (brew) not found')
        except json.JSONDecodeError:
            logger.warning('Could not parse installed formulas list')
        except Exception as e:
            logger.error(f'Failed to list formulas: {e}')

        return packages

    @staticmethod
    def _get_formula_version(formula: str) -> str | None:
        """Gets the installed version for a formula.

        Args:
            formula: The formula name

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['brew', 'info', formula, '--json=v2'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                info = json.loads(result.stdout)
                formulas = info.get('formulae', [])
                if formulas:
                    installed = formulas[0].get('installed', [])
                    if installed:
                        return installed[0].get('version')
        except Exception:
            pass

        return None
