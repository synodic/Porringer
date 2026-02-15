"""Intermediate base for Python-ecosystem environment plugins.

Plugins that install Python packages (pip, uv, pipx) share several
boilerplate declarations:

* `ecosystem() -> Ecosystem('python')`
* `package_name_validator() -> 'pep440'`
* `consumed_runtime_kind() -> 'python'`
* `_discover_venv_python()` — locating the interpreter in a project's
  `.venv` directory.

`PythonEnvironment` bundles these once so that concrete plugins can
focus on their tool-specific behaviour.
"""

import sys
from pathlib import Path

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import Ecosystem


class PythonEnvironment(Environment, RuntimeConsumer):
    """Base for plugins that install packages into a Python environment.

    Provides default implementations for the common `ecosystem`,
    `package_name_validator`, `consumed_runtime_kind`, and
    `_discover_venv_python` methods shared by pip, uv, and pipx.

    Subclasses must still implement the `Environment` abstract methods
    (`install_command`, `upgrade_command`, `packages`), as well as
    `tool_name()`.
    """

    @staticmethod
    def ecosystem() -> Ecosystem:
        """Python-ecosystem plugins all share the `'python'` ecosystem."""
        return Ecosystem('python')

    @staticmethod
    def package_name_validator() -> str:
        """Python packages use PEP 440 validation."""
        return 'pep440'

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        """Python environment plugins consume a Python runtime."""
        return 'python'

    @property
    def python_command(self) -> str:
        """The Python interpreter command to target.

        Returns the runtime override path when set by a `RuntimeProvider`,
        otherwise falls back to the running interpreter (`sys.executable`).
        """
        if self.runtime_executable is not None:
            return str(self.runtime_executable)
        return sys.executable

    @staticmethod
    def _discover_venv_python(project_path: Path) -> Path | None:
        """Discover the Python interpreter inside a project's virtual environment.

        Looks for a `.venv` directory under *project_path* and returns
        the path to its Python executable if found.

        Args:
            project_path: Root directory of the project.

        Returns:
            Path to the venv Python, or `None` if no venv is found.
        """
        venv_dir = project_path / '.venv'
        if not venv_dir.is_dir():
            return None

        if sys.platform == 'win32':
            python = venv_dir / 'Scripts' / 'python.exe'
        else:
            python = venv_dir / 'bin' / 'python'

        return python if python.is_file() else None
