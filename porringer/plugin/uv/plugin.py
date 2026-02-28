"""Plugin implementation"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.schema import Package, PackageRef, PluginParameters


class UvEnvironment(PythonEnvironment):
    """Represents a Python environment managed by uv.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using uv
    as the backend package manager.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the uv environment plugin."""
        super().__init__(parameters)

    def _python_args(self) -> list[str]:
        """Return `['--python', '<path>']` when an override is active.

        Uses `self.python_command` (inherited from `PythonEnvironment`)
        to check whether a runtime override is set.
        """
        if self.runtime_executable is not None:
            return ['--python', self.python_command]
        return []

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV wraps the `uv` CLI."""
        return 'uv'

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to install a package via uv."""
        cmd = ['uv', 'pip', 'install', *self._python_args(), package.specifier]
        if include_prereleases:
            cmd.append('--prerelease=allow')
        return cmd

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Returns the CLI command to upgrade a package via uv."""
        cmd = [
            'uv',
            'pip',
            'install',
            '--upgrade',
            *self._python_args(),
            package.specifier,
        ]
        if include_prereleases:
            cmd.append('--prerelease=allow')
        return cmd

    @override
    def uninstall_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to uninstall a package via uv."""
        return ['uv', 'pip', 'uninstall', *self._python_args(), package.name]

    @override
    async def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages using `uv pip list --format=json`.

        When *project_path* is provided, the method discovers the
        project's virtual environment (`<project_path>/.venv`) and
        lists packages from that interpreter.  Otherwise it falls back
        to the runtime-override or the default Python.

        Args:
            project_path: Optional project directory.  When set, the
                listing is scoped to the project's `.venv`.

        Returns:
            A list of installed packages.
        """
        # Determine the effective Python target
        effective_args = self._python_args()
        if project_path is not None:
            venv_python = self._discover_venv_python(project_path)
            if venv_python is not None:
                effective_args = ['--python', str(venv_python)]

        entries = await self._run_json_command(['uv', 'pip', 'list', '--format=json', *effective_args])
        if isinstance(entries, list):
            return [Package(name=e['name'], version=e.get('version')) for e in entries]

        return []
