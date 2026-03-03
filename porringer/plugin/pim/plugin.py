"""Plugin implementation for Python Install Manager (pymanager)"""

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import override

from packaging.version import InvalidVersion, Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext, RuntimeProvider
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginDependency, PluginKind

# Architecture suffix pattern: a trailing dash followed by digits (e.g. "-64", "-32").
_ARCH_SUFFIX = re.compile(r'-\d+$')

logger = logging.getLogger(__name__)


class PIMEnvironment(Environment, RuntimeProvider):
    """Represents a Python runtime environment managed by Python Install Manager (pymanager).

    Provides methods to install, search, uninstall, upgrade, and list Python runtimes using
    the official Python Install Manager (py/pymanager) as the backend.

    This plugin is Windows-only and requires winget to be available for installing
    the Python Install Manager itself.

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions.

    CLI Reference:
        - py list [-f=<FMT>] [--online] [<TAG>...]  - List installed/available runtimes
        - py install [-f|--force] [-u|--update] [--dry-run] [<TAG>...]  - Install runtimes
        - py uninstall [-y|--yes] <TAG>...  - Uninstall runtimes
        - pymanager install 9NQ7512CXL7T  - Install via winget (Store app ID)
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """PIM belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @staticmethod
    @override
    def plugin_kind() -> PluginKind:
        """PIM manages language runtimes."""
        return PluginKind.RUNTIME

    @staticmethod
    @override
    def is_supported() -> bool:
        """Supported on Windows only."""
        return sys.platform == 'win32'

    @staticmethod
    @override
    def package_name_validator() -> str:
        """Python runtimes use PEP 440 validation."""
        return 'pep440'

    @classmethod
    @override
    def provided_runtime_kind(cls) -> str:
        """PIM provides Python runtimes."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """PIM wraps the `py` CLI."""
        return 'py'

    @staticmethod
    @override
    def dependencies() -> list[PluginDependency]:
        """Declares plugin dependencies.

        This plugin requires Windows and depends on winget when on Windows
        for installing the Python Install Manager.

        Returns:
            A list of plugin dependencies
        """
        return [
            PluginDependency(
                plugin='winget',
                required=True,
                platforms=['win32'],
            ),
        ]

    @override
    async def resolve_executable(self, tag: str) -> Path | None:
        """Return the path to the Python interpreter for a managed runtime.

        Uses `py -<tag> -c "import sys; print(sys.executable)"` to ask
        the Python Install Manager where the given runtime lives.

        Args:
            tag: The Python version tag (e.g. `"3.14"`, `"3.12"`).

        Returns:
            Absolute path to the interpreter, or `None` if not installed.
        """
        logger = logging.getLogger('porringer.pim.resolve_executable')
        try:
            proc = await asyncio.create_subprocess_exec(
                'py',
                f'-{tag}',
                '-c',
                'import sys; print(sys.executable)',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace').strip() if stderr_bytes else ''
                logger.debug('py -%s failed: %s', tag, stderr)
                return None
            stdout = stdout_bytes.decode('utf-8', errors='replace').strip() if stdout_bytes else ''
            path = Path(stdout)
            if path.exists():
                return path
            logger.warning('py -%s resolved to %s but it does not exist', tag, path)
        except FileNotFoundError:
            logger.debug('py launcher not found')
        except Exception as e:
            logger.debug('resolve_executable failed for tag %s: %s', tag, e)
        return None

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to install a Python runtime via pymanager."""
        return ['py', 'install', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to upgrade a Python runtime via pymanager."""
        return ['py', 'install', '--update', package.name]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Returns the CLI command to uninstall a Python runtime via pymanager."""
        return ['py', 'uninstall', '-y', package.name]

    @override
    def sort_tags(self, tags: list[str]) -> list[str]:
        """Filter and sort PIM tags, handling architecture suffixes.

        The ``py`` launcher emits tags like ``"3.14-64"`` or
        ``"3.12-32"`` where the trailing ``-<arch>`` component is an
        architecture discriminator, not part of the :pep:`440` version.
        This override strips that suffix for version parsing while
        preserving the **full** tag in the returned list so that
        :meth:`resolve_executable` receives the architecture-qualified
        string (e.g. ``py -3.14-64``).

        Tags that remain unparseable after suffix stripping (e.g.
        ``"(venv)"``) are silently dropped.
        """
        parsed: list[tuple[Version, str]] = []
        for tag in tags:
            version_part = _ARCH_SUFFIX.sub('', tag)
            try:
                parsed.append((Version(version_part), tag))
            except InvalidVersion:
                logger.debug('Dropping unparseable tag %r from %s', tag, type(self).__name__)
        parsed.sort(key=lambda pair: pair[0], reverse=True)
        return [tag for _, tag in parsed]

    @override
    async def available_tags(self) -> list[str]:
        """Return all Python tags the ``py`` launcher can resolve.

        Runs ``py list -f json`` **without** ``--only-managed`` so that
        runtimes installed via the official python.org installer,
        Microsoft Store, or any other source are included — not just
        those managed by pymanager.

        The returned list may contain non-version strings (e.g.
        ``"(venv)"``) emitted by the ``py`` launcher.
        :meth:`~RuntimeProvider.sort_tags` is responsible for filtering
        and ordering before the builder attempts resolution.

        Returns:
            A list of raw tag strings as reported by the launcher.
        """
        data = await self._run_json_command(['py', 'list', '-f', 'json'])
        if not isinstance(data, dict):
            return []

        return [runtime.get('tag', '') for runtime in data.get('versions', []) if runtime.get('tag')]

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for newer Python runtimes via ``py list --online``.

        Queries the Python Install Manager's online listing and finds
        the highest available version for each requested tag.

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        data = await self._run_json_command(['py', 'list', '--online', '-f', 'json'])
        if not isinstance(data, dict):
            return []

        results: list[Package] = []
        for pkg_ref in params.packages:
            best_version: str | None = None
            for runtime in data.get('versions', []):
                tag = runtime.get('tag', '')
                version = runtime.get('sort-version') or tag
                if (tag.startswith(pkg_ref.name) or pkg_ref.name == tag) and (
                    best_version is None or version > best_version
                ):
                    best_version = version
            if best_version is not None:
                results.append(Package(name=pkg_ref.name, version=best_version))
        return results

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        """Lists all installed Python runtimes.

        pim manages Python runtimes globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  pim is inherently global.
            runtime_context: Unused.  pim manages runtimes, not
                per-interpreter packages.

        Returns:
            A list of installed Python runtime packages
        """
        data = await self._run_json_command(['py', 'list', '--only-managed', '-f', 'json'])
        if not isinstance(data, dict):
            return []

        packages: list[Package] = []
        for runtime in data.get('versions', []):
            tag = runtime.get('tag', 'unknown')
            version = runtime.get('sort-version') or tag
            packages.append(Package(name=tag, version=version))
        return packages

    @classmethod
    async def _get_runtime_version(cls, tag: str) -> str | None:
        """Gets the actual version string for an installed runtime.

        Args:
            tag: The Python version tag (e.g., "3.12")

        Returns:
            The version string, or None if not found
        """
        data = await cls._run_json_command(['py', 'list', '--only-managed', '-f', 'json', tag])
        if not isinstance(data, dict):
            return None
        runtimes = data.get('versions', [])
        if runtimes:
            return runtimes[0].get('sort-version') or runtimes[0].get('tag')
        return None
