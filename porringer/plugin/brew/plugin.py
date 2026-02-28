"""Plugin implementation for Homebrew (brew) package manager."""

import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    CheckUpdatesParameters,
    Environment,
    PackageParameters,
)
from porringer.core.schema import Ecosystem, Package, PackageRef


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
    def ecosystem() -> Ecosystem:
        """Homebrew belongs to the `system` ecosystem."""
        return Ecosystem('system')

    @staticmethod
    @override
    def is_supported() -> bool:
        """Supported on macOS and Linux, not on Windows."""
        return sys.platform != 'win32'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Homebrew wraps the `brew` CLI."""
        return 'brew'

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to install a package via brew."""
        return ['brew', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to upgrade a package via brew."""
        return ['brew', 'upgrade', package.name]

    @override
    def uninstall_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to uninstall a package via brew."""
        return ['brew', 'uninstall', package.name]

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available formula updates via ``brew outdated``.

        Uses ``brew outdated --json`` to discover formulas with
        available updates, then resolves the latest upstream version
        via ``brew info --json=v2``.

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        outdated = await self._run_json_command(['brew', 'outdated', '--json'])
        if not isinstance(outdated, list):
            return []

        requested = {p.name.lower() for p in params.packages} if params.packages else None
        results: list[Package] = []
        for entry in outdated:
            name = entry.get('name', '')
            if requested is not None and name.lower() not in requested:
                continue
            # Resolve latest upstream version via brew info
            info = await self._run_json_command(['brew', 'info', name, '--json=v2'])
            if isinstance(info, dict):
                formulas = info.get('formulae', [])
                if formulas:
                    latest = formulas[0].get('versions', {}).get('stable')
                    if latest:
                        results.append(Package(name=name, version=latest))
                        continue
            # Fallback: use current_version from outdated entry
            current = entry.get('current_version')
            if current:
                results.append(Package(name=name, version=current))
        return results

    @override
    async def install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs a formula using Homebrew.

        Delegates streaming to the base class, then resolves the
        installed version via `brew info`.
        """
        result = await super().install(params)
        if result is not None and result.version is None:
            result = Package(
                name=result.name,
                version=await self.__class__._get_formula_version(result.name),
            )
        return result

    @override
    async def upgrade(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrades a formula using Homebrew.

        Delegates streaming to the base class, then resolves the
        installed version via `brew info`.
        """
        result = await super().upgrade(params)
        if result is not None and result.version is None:
            result = Package(
                name=result.name,
                version=await self.__class__._get_formula_version(result.name),
            )
        return result

    @override
    async def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Lists all installed formulas in Homebrew.

        Homebrew manages system packages globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  Homebrew is inherently global.

        Returns:
            A list of installed packages
        """
        formulas = await self._run_json_command(['brew', 'list', '--formula', '--json'])
        if not isinstance(formulas, list):
            return []

        packages: list[Package] = []
        for formula in formulas:
            name = formula.get('name', 'unknown')
            installed = formula.get('installed', [])
            version = installed[0].get('version', 'unknown') if installed else 'unknown'
            packages.append(Package(name=name, version=version))
        return packages

    @classmethod
    async def _get_formula_version(cls, formula: str) -> str | None:
        """Gets the installed version for a formula.

        Args:
            formula: The formula name

        Returns:
            The version string, or None if not found
        """
        info = await cls._run_json_command(['brew', 'info', formula, '--json=v2'])
        if not isinstance(info, dict):
            return None
        formulas = info.get('formulae', [])
        if formulas:
            installed = formulas[0].get('installed', [])
            if installed:
                return installed[0].get('version')
        return None
