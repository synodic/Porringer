"""Plugin implementation for Deno environment."""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Ecosystem, Package, PackageRef


class DenoEnvironment(Environment):
    """Represents a Deno environment.

    Provides methods to install, uninstall, and upgrade global scripts
    using `deno install -g`.  Deno supports both npm (`npm:`) and
    JSR (`jsr:`) package sources.  By default, bare package names are
    prefixed with `npm:` unless they already carry a source prefix.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Deno belongs to the `deno` ecosystem."""
        return Ecosystem('deno')

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Deno wraps the `deno` CLI."""
        return 'deno'

    @staticmethod
    def _deno_specifier(package: PackageRef) -> str:
        """Build a Deno-compatible package specifier.

        Prefixes the package name with `npm:` when no source prefix
        (`npm:`, `jsr:`, `https:`) is present.  Appends the
        constraint with `@` separator if provided.
        """
        name = package.name
        if not any(name.startswith(p) for p in ('npm:', 'jsr:', 'https:', 'http:')):
            name = f'npm:{name}'
        if package.constraint:
            return f'{name}@{package.constraint}'
        return name

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a global script via Deno."""
        return ['deno', 'install', '-g', self._deno_specifier(package)]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a global script via Deno."""
        return ['deno', 'install', '-g', '--force', self._deno_specifier(package)]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers globally installed Deno scripts.

        Deno does not provide a structured list of globally installed
        scripts; returns an empty list.  *project_path* is accepted
        for interface compatibility.

        Args:
            project_path: Unused.

        Returns:
            An empty list.
        """
        return []
