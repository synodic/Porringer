"""Plugin implementation"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import Package, PackageRef, PluginParameters


class UvEnvironment(Environment, RuntimeConsumer):
    """Represents a Python environment managed by uv.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using uv
    as the backend package manager.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the uv environment plugin."""
        super().__init__(parameters)
        self._cached_packages: list[Package] | None = None
        self._cached_python: str | None = None

    def _python_args(self) -> list[str]:
        """Return `['--python', '<path>']` when an override is active.

        Reads from `self.runtime_executable` (set by the sync engine
        via a runtime provider).  Returns an empty list when no override
        is set.
        """
        if self.runtime_executable is not None:
            return ['--python', str(self.runtime_executable)]
        return []

    @staticmethod
    @override
    def ecosystem() -> str:
        """UV belongs to the `python` ecosystem."""
        return 'python'

    @staticmethod
    @override
    def default_priority() -> int:
        """UV is the preferred Python installer (priority 10)."""
        return 10

    @staticmethod
    @override
    def package_name_validator() -> str:
        """Python packages use PEP 440 validation."""
        return 'pep440'

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """UV consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV wraps the `uv` CLI."""
        return 'uv'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via uv."""
        return ['uv', 'pip', 'install', *self._python_args(), package.specifier]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via uv."""
        return [
            'uv',
            'pip',
            'install',
            '--upgrade',
            *self._python_args(),
            package.specifier,
        ]

    @staticmethod
    def _discover_venv_python(project_path: Path) -> Path | None:
        """Discover the Python interpreter inside a project's virtual environment.

        Looks for a ``.venv`` directory under *project_path* and returns
        the path to its Python executable if found.

        Args:
            project_path: Root directory of the project.

        Returns:
            Path to the venv Python, or ``None`` if no venv is found.
        """
        venv_dir = project_path / '.venv'
        if not venv_dir.is_dir():
            return None

        if sys.platform == 'win32':
            python = venv_dir / 'Scripts' / 'python.exe'
        else:
            python = venv_dir / 'bin' / 'python'

        return python if python.is_file() else None

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages using ``uv pip list --format=json``.

        When *project_path* is provided, the method discovers the
        project's virtual environment (``<project_path>/.venv``) and
        lists packages from that interpreter.  Otherwise it falls back
        to the runtime-override or the default Python.

        Results are cached per effective interpreter so multiple calls
        within a single sync run don't shell out repeatedly.

        Args:
            project_path: Optional project directory.  When set, the
                listing is scoped to the project's ``.venv``.

        Returns:
            A list of installed packages.
        """
        # Determine the effective Python target
        effective_args = self._python_args()
        if project_path is not None:
            venv_python = self._discover_venv_python(project_path)
            if venv_python is not None:
                effective_args = ['--python', str(venv_python)]

        cache_key = str(effective_args)
        if self._cached_packages is not None and self._cached_python == cache_key:
            return self._cached_packages

        logger = logging.getLogger('porringer.uv.packages')
        try:
            args = ['uv', 'pip', 'list', '--format=json', *effective_args]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.error('uv pip list failed: %s', result.stderr)
                self._cached_packages = []
                self._cached_python = cache_key
                return self._cached_packages

            entries: list[dict[str, str]] = json.loads(result.stdout)
            self._cached_packages = [Package(name=entry['name'], version=entry.get('version')) for entry in entries]
        except FileNotFoundError:
            logger.error('uv not found on PATH')
            self._cached_packages = []
        except (json.JSONDecodeError, subprocess.SubprocessError) as e:
            logger.error('Failed to list uv packages: %s', e)
            self._cached_packages = []

        self._cached_python = cache_key
        return self._cached_packages
