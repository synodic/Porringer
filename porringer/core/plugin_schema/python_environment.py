"""Intermediate base for Python-ecosystem environment plugins.

Plugins that install Python packages (pip, uv, pipx) share several
boilerplate declarations:

* `ecosystem() -> Ecosystem('python')`
* `package_name_validator() -> 'pep440'`
* `consumed_runtime_kind() -> 'python'`
* `_discover_venv_python()` — locating the interpreter in a project's
  `.venv` directory.
* `_check_pypi_updates()` — querying PyPI for newer package versions.

`PythonEnvironment` bundles these once so that concrete plugins can
focus on their tool-specific behaviour.
"""

import logging
import sys
from pathlib import Path

import httpx
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import Ecosystem, Package


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

    def _check_pypi_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Query the PyPI JSON API for newer versions of the requested packages.

        For each package in *params.packages*, fetches
        ``https://pypi.org/pypi/{name}/json`` and determines the latest
        available version.  When *params.include_prereleases* is
        ``False`` (default), only the stable ``info.version`` is
        returned.  When ``True``, the highest version across all
        ``releases`` keys is selected (including dev/alpha/beta/rc).

        This helper is shared by pip, uv, and pipx plugins.

        Args:
            params: The check parameters including which packages to
                check and whether to include pre-releases.

        Returns:
            A list of packages with their ``version`` set to the latest
            available on PyPI.
        """
        logger = logging.getLogger(f'porringer.{self.tool_name()}.check_pypi')
        results: list[Package] = []

        for pkg_ref in params.packages:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(f'https://pypi.org/pypi/{pkg_ref.name}/json')
                    response.raise_for_status()
                    data = response.json()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.debug('PyPI query failed for %s: %s', pkg_ref.name, exc)
                continue

            if params.include_prereleases:
                # Scan all release keys for the highest version
                releases = data.get('releases', {})
                best: Version | None = None
                for ver_str in releases:
                    try:
                        ver = Version(ver_str)
                    except InvalidVersion:
                        continue
                    if best is None or ver > best:
                        best = ver
                if best is not None:
                    results.append(Package(name=pkg_ref.name, version=str(best)))
            else:
                # Stable-only: use info.version
                version_str = data.get('info', {}).get('version')
                if version_str:
                    results.append(Package(name=pkg_ref.name, version=version_str))

        return results

    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying PyPI.

        Default implementation for all Python-ecosystem plugins.
        Subclasses can override to use native tooling (e.g.
        ``pip list --outdated``) and fall back to this via ``super()``.

        Args:
            params: The check parameters including which packages to check.

        Returns:
            A list of packages that have updates available.
        """
        return self._check_pypi_updates(params)
