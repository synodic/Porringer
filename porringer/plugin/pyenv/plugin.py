"""Plugin implementation for pyenv-managed Python runtimes."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import override

from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext, RuntimeProvider
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind


class PyenvEnvironment(Environment, RuntimeProvider):
    """Manages Python runtimes via pyenv (Linux / macOS).

    Uses `pyenv` to install, list, and manage Python interpreter
    versions.  Implements `RuntimeProvider` so the sync engine
    can resolve the filesystem path to a managed interpreter.

    CLI Reference:
        - pyenv versions --bare           — list installed versions
        - pyenv install [-s] <version>    — install a version
        - pyenv uninstall -f <version>    — remove a version
        - pyenv prefix <version>          — print install prefix
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Pyenv belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @staticmethod
    @override
    def plugin_kind() -> PluginKind:
        """Pyenv manages language runtimes."""
        return PluginKind.RUNTIME

    @staticmethod
    @override
    def is_supported() -> bool:
        """Supported on Unix, not on Windows."""
        return sys.platform != 'win32'

    @staticmethod
    @override
    def package_name_validator() -> str:
        """Python runtimes use PEP 440 validation."""
        return 'pep440'

    @classmethod
    @override
    def provided_runtime_kind(cls) -> str:
        """Pyenv provides Python runtimes."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pyenv wraps the `pyenv` CLI."""
        return 'pyenv'

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to install a Python runtime via pyenv."""
        return ['pyenv', 'install', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to upgrade (reinstall) a Python runtime via pyenv."""
        return ['pyenv', 'install', '--skip-existing', package.name]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Returns the CLI command to uninstall a Python runtime via pyenv."""
        return ['pyenv', 'uninstall', '-f', package.name]

    # ------------------------------------------------------------------
    # RuntimeProvider
    # ------------------------------------------------------------------

    @override
    async def resolve_executable(self, tag: str) -> Path | None:
        """Return the path to the Python interpreter for a pyenv-managed runtime.

        Uses `pyenv prefix <tag>` to find the install directory, then
        appends `bin/python`.

        Args:
            tag: The Python version (e.g. `"3.14.0"`, `"3.12.4"`).

        Returns:
            Absolute path to the interpreter, or `None` if not installed.
        """
        logger = logging.getLogger('porringer.pyenv.resolve_executable')
        try:
            proc = await asyncio.create_subprocess_exec(
                'pyenv',
                'prefix',
                tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace').strip() if stderr_bytes else ''
                logger.debug('pyenv prefix %s failed: %s', tag, stderr)
                return None
            stdout = stdout_bytes.decode('utf-8', errors='replace').strip() if stdout_bytes else ''
            prefix = Path(stdout)
            executable = prefix / 'bin' / 'python'
            if executable.exists():
                return executable
            logger.warning('pyenv prefix %s resolved to %s but bin/python missing', tag, prefix)
        except FileNotFoundError:
            logger.debug('pyenv not found on PATH')
        except Exception as e:
            logger.debug('resolve_executable failed for tag %s: %s', tag, e)
        return None

    @override
    async def available_tags(self) -> list[str]:
        """Return all pyenv-installed version tags.

        Since pyenv only tracks its own installations there is no
        distinction between "managed" and "available" — this is
        equivalent to extracting names from ``packages()``.

        Returns:
            A list of version strings (e.g. ``["3.14.0", "3.12.4"]``).
        """
        output = await self._run_text_command(['pyenv', 'versions', '--bare'])
        if output is None:
            return []

        return [version for line in output.splitlines() if (version := line.strip())]

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for newer Python versions via ``pyenv install --list``.

        Parses the available versions from ``pyenv install --list`` and
        finds the highest version matching each requested package's
        major.minor prefix.

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        output = await self._run_text_command(['pyenv', 'install', '--list'])
        if output is None:
            return []

        available: list[tuple[Version, str]] = []
        for line in output.splitlines():
            ver_str = line.strip()
            if not ver_str:
                continue
            try:
                ver = Version(ver_str)
            except InvalidVersion:
                continue
            # Skip pre-releases unless requested
            if ver.is_prerelease and not params.include_prereleases:
                continue
            available.append((ver, ver_str))

        results: list[Package] = []
        for pkg_ref in params.packages:
            # Match by prefix (e.g. "3.12" matches "3.12.x")
            best: Version | None = None
            best_str: str = ''
            for ver, ver_str in available:
                if (ver_str.startswith(pkg_ref.name) or pkg_ref.name == ver_str) and (best is None or ver > best):
                    best = ver
                    best_str = ver_str
            if best is not None:
                results.append(Package(name=pkg_ref.name, version=best_str))
        return results

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        """Lists installed Python runtimes via `pyenv versions --bare`.

        pyenv manages Python runtimes globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  pyenv is inherently global.
            runtime_context: Unused.  pyenv manages runtimes, not
                per-interpreter packages.

        Returns:
            A list of installed Python runtime packages.
        """
        output = await self._run_text_command(['pyenv', 'versions', '--bare'])
        if output is None:
            return []

        return [Package(name=version, version=version) for line in output.splitlines() if (version := line.strip())]
