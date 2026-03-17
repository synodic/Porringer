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
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.schema import Ecosystem, Package, PackageRef


def _pick_highest_version(
    releases: dict[str, object], *, stable_only: bool
) -> Version | None:
    """Return the highest ``Version`` from *releases* keys, or ``None``.

    Args:
        releases: The ``releases`` dict from PyPI's JSON API.
        stable_only: When ``True``, skip pre-release and dev versions.
    """
    best: Version | None = None
    for ver_str in releases:
        try:
            ver = Version(ver_str)
        except InvalidVersion:
            continue
        if stable_only and (ver.is_prerelease or ver.is_devrelease):
            continue
        if best is None or ver > best:
            best = ver
    return best


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
        return Ecosystem("python")

    @staticmethod
    def package_name_validator() -> str:
        """Python packages use PEP 440 validation."""
        return "pep440"

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        """Python environment plugins consume a Python runtime."""
        return "python"

    @classmethod
    def is_available_for(cls, runtime_context: RuntimeContext) -> bool:
        """Check whether this plugin can operate with the given runtime.

        Determines the target Python interpreter from *runtime_context*
        (falling back to ``sys.executable``) and verifies that the
        plugin's underlying tool is importable in that interpreter.

        Subclasses that wrap a standalone binary (e.g. ``uv``) rather
        than a Python module should override this to delegate to
        ``is_available()`` instead.

        Args:
            runtime_context: Resolved runtime paths for this execution.

        Returns:
            ``True`` if the tool module (identified by ``tool_name()``)
            is importable in the target interpreter.
        """
        tool = cls.tool_name()
        if tool is None:
            return True

        # Determine which interpreter to probe
        exe = runtime_context.get(cls.consumed_runtime_kind())
        python = str(exe) if exe is not None else sys.executable

        return cls._probe_module(python, tool)

    @staticmethod
    def _probe_module(python: str, module: str) -> bool:
        """Check whether *module* is importable in the interpreter at *python*.

        Uses a lightweight subprocess invocation with ``-c "import <module>"``
        to avoid loading the module into the current process.

        This is called during deferred resolution (in a thread via
        ``asyncio.to_thread``), so blocking briefly on a subprocess
        is acceptable.

        Args:
            python: Path or command for the Python interpreter.
            module: The module name to check (e.g. ``'pip'``).

        Returns:
            ``True`` if the import succeeds, ``False`` otherwise.
        """
        try:
            result = subprocess.run(
                [python, "-c", f"import {module}"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except OSError, subprocess.SubprocessError:
            return False

    def python_command(self, runtime_context: RuntimeContext | None = None) -> str:
        """The Python interpreter command to target.

        Returns the runtime override path from *runtime_context* when
        a matching runtime kind is available, otherwise falls back to
        the running interpreter (``sys.executable``).

        Args:
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use the current process interpreter.
        """
        _logger = logging.getLogger("porringer.python_environment")
        kind = self.consumed_runtime_kind()
        if runtime_context is not None:
            exe = runtime_context.get(kind)
            if exe is not None:
                _logger.debug(
                    "python_command: using runtime override %s for kind=%s", exe, kind
                )
                return str(exe)
            _logger.debug(
                "python_command: runtime_context present but no entry for kind=%s", kind
            )
        else:
            _logger.debug(
                "python_command: no runtime_context supplied, falling back to sys.executable"
            )

        # In frozen applications (e.g. PyInstaller), sys.executable is
        # the packaged binary — not a Python interpreter.  Attempt to
        # find a real Python on PATH before falling back.
        if getattr(sys, "frozen", False):
            _logger.debug(
                "python_command: frozen application detected, trying shutil.which"
            )
            which_python = shutil.which("python")
            if which_python is not None:
                _logger.debug("python_command: using PATH python %s", which_python)
                return which_python
            _logger.debug(
                "python_command: shutil.which found no python, falling back to sys.executable"
            )

        return sys.executable

    def package_python(self, package_name: str) -> str | None:
        """Return the Python interpreter for a specific installed package.

        Environments that install each package into its own isolated
        venv (e.g. pipx) should override this to return the interpreter
        from within *that package's* venv.  The default returns
        ``None``, meaning the caller should fall back to
        :meth:`python_command`.

        Args:
            package_name: The name of the installed CLI tool / package.

        Returns:
            Path to the package-specific Python interpreter, or
            ``None`` when this environment does not use per-package
            venvs.
        """
        return None

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
        venv_dir = project_path / ".venv"
        if not venv_dir.is_dir():
            return None

        if sys.platform == "win32":
            python = venv_dir / "Scripts" / "python.exe"
        else:
            python = venv_dir / "bin" / "python"

        return python if python.is_file() else None

    async def _check_pypi_updates(
        self, params: CheckUpdatesParameters
    ) -> list[Package]:
        """Query the PyPI JSON API for newer versions of the requested packages.

        For each package in *params.packages*, fetches
        ``https://pypi.org/pypi/{name}/json`` and determines the latest
        available version.  When *params.include_prereleases* is
        ``False`` (default), only the stable ``info.version`` is
        returned.  When ``True``, the highest version across all
        ``releases`` keys is selected (including dev/alpha/beta/rc).

        Uses ``httpx.AsyncClient`` so the event loop is never blocked
        by network I/O.

        This helper is shared by pip, uv, and pipx plugins.

        Args:
            params: The check parameters including which packages to
                check and whether to include pre-releases.

        Returns:
            A list of packages with their ``version`` set to the latest
            available on PyPI.
        """
        logger = logging.getLogger(f"porringer.{self.tool_name()}.check_pypi")
        results: list[Package] = []

        shared = params.http_client

        async def _run(client: httpx.AsyncClient) -> list[Package]:
            inner: list[Package] = []
            for pkg_ref in params.packages:
                pkg = await self._check_single_pypi_package(
                    client, pkg_ref, params.include_prereleases, logger
                )
                if pkg is not None:
                    inner.append(pkg)
            return inner

        if shared is not None:
            results = await _run(shared)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                results = await _run(client)

        return results

    @staticmethod
    async def _check_single_pypi_package(
        client: httpx.AsyncClient,
        pkg_ref: PackageRef,
        include_prereleases: bool,
        logger: logging.Logger,
    ) -> Package | None:
        """Fetch one package from PyPI and return the latest version, or ``None``."""
        try:
            response = await client.get(f"https://pypi.org/pypi/{pkg_ref.name}/json")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.debug("PyPI query failed for %s: %s", pkg_ref.name, exc)
            return None

        if include_prereleases:
            best = _pick_highest_version(data.get("releases", {}), stable_only=False)
            return (
                Package(name=pkg_ref.name, version=str(best))
                if best is not None
                else None
            )

        # Stable-only: prefer info.version when it is itself stable.
        version_str = data.get("info", {}).get("version")
        if version_str:
            try:
                if not Version(version_str).is_prerelease:
                    return Package(name=pkg_ref.name, version=version_str)
            except InvalidVersion:
                return Package(name=pkg_ref.name, version=version_str)

        # info.version was a pre-release or missing — scan releases.
        best_stable = _pick_highest_version(data.get("releases", {}), stable_only=True)
        if best_stable is not None:
            return Package(name=pkg_ref.name, version=str(best_stable))
        return None

    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying PyPI.

        Default implementation for all Python-ecosystem plugins.
        Subclasses can override to use native tooling (e.g.
        ``pip list --outdated``) and fall back to this via ``super()``.

        Args:
            params: The check parameters including which packages to check.

        Returns:
            A list of packages that have updates available.
        """
        return await self._check_pypi_updates(params)
