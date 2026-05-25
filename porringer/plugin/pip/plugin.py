"""Plugin integration for plugin."""

"""Plugin implementation."""

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, override

from porringer.core.plugin_schema.environment import (
    CheckUpdatesParameters,
    PackageParameters,
    PackageVerb,
)
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Package, PackageRef
from porringer.schema import ActionProgress, SetupAction
from porringer.utility.utility import run_command

# Regex patterns for parsing pip output
_DOWNLOADING_PATTERN = re.compile(
    r'Downloading\s+(\S+)\s+\(([^)]+)\)',
)
_DOWNLOAD_PROGRESS_PATTERN = re.compile(
    r'(\d+(?:\.\d+)?)\s*[kMG]?B.*?(\d+)%',
)
_INSTALLING_PATTERN = re.compile(
    r'Installing collected packages?:\s*(.*)',
)
_ALREADY_SATISFIED_PATTERN = re.compile(
    r'Requirement already satisfied',
)


class PIPEnvironment(PythonEnvironment):
    """Represents a Python environment managed by pip.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using pip
    as the backend package manager.

    This plugin can optionally use a Python runtime provider (like pim) for managing
    the underlying Python installation.
    """

    @classmethod
    @override
    def auxiliary_tools(cls) -> Sequence[str]:
        """Pip may invoke ``pymanager`` to refresh global aliases."""
        return ('pymanager',)

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pip wraps the `pip` CLI."""
        return 'pip'

    @classmethod
    @override
    def is_available(cls) -> bool:
        """Check if pip is on PATH as a standalone executable.

        This is the **initial discovery** check used before any runtime
        has been resolved.  It only looks for a ``pip`` executable on
        PATH.

        Runtime-aware availability (e.g. ``python -m pip`` via a
        resolved interpreter) is handled by
        :meth:`~PythonEnvironment.is_available_for`, which is called
        during deferred resolution after the RUNTIME phase.

        Returns:
            True if ``pip`` is found on PATH, False otherwise.
        """
        return shutil.which('pip') is not None

    @override
    def install_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command to install a package via pip."""
        cmd = [self.python_command(runtime_context), '-m', 'pip', 'install', package.specifier]
        if include_prereleases:
            cmd.append('--pre')
        return cmd

    @override
    def upgrade_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command to upgrade a package via pip."""
        cmd = [
            self.python_command(runtime_context),
            '-m',
            'pip',
            'install',
            '--upgrade',
            package.specifier,
        ]
        if include_prereleases:
            cmd.append('--pre')
        return cmd

    @override
    def uninstall_command(
        self,
        package: PackageRef,
        *,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command to uninstall a package via pip."""
        return [self.python_command(runtime_context), '-m', 'pip', 'uninstall', '-y', package.name]

    @staticmethod
    @override
    def supports_parallel() -> bool:
        """Pip does not support parallel installs safely due to potential conflicts."""
        return False

    @override
    def dry_run_flags(self, verb: PackageVerb) -> Sequence[str]:
        """Pip natively supports ``--dry-run`` for install / upgrade."""
        if verb in {'install', 'upgrade'}:
            return ('--dry-run',)
        return ()

    @override
    def parse_progress_line(
        self,
        line: str,
        channel: Literal['stdout', 'stderr'],
        action: SetupAction,
    ) -> ActionProgress | None:
        """Translate a pip stderr line into a structured progress event."""
        if channel != 'stderr':
            return None
        captured: list[ActionProgress] = []
        self._parse_progress_line(line, action, captured.append)
        return captured[0] if captured else None

    @override
    async def post_action(
        self,
        verb: PackageVerb,
        params: PackageParameters,
        success: bool,
    ) -> None:
        """On Windows, refresh ``pymanager`` global aliases after install/upgrade.

        Best-effort.  Skipped on dry-run, on failure, and for
        uninstall.  Failures never block the calling action.
        """
        if not success or params.dry or verb == 'uninstall':
            return
        await self._refresh_pymanager_aliases(logging.getLogger(f'porringer.pip.{verb}'))

    @staticmethod
    async def _refresh_pymanager_aliases(logger: logging.Logger) -> None:
        """Best-effort refresh of pymanager global aliases after pip install.

        No-op when pymanager is absent.  Failures never block the install.
        """
        if shutil.which('pymanager') is None:
            return

        try:
            result = await run_command(['pymanager', 'install', '--refresh'])
            if result.returncode != 0:
                logger.warning('pymanager install --refresh exited with code %d', result.returncode)
        except Exception as exc:
            logger.warning('Failed to refresh pymanager aliases: %s', exc)

    @staticmethod
    def _parse_progress_line(
        line: str,
        action: SetupAction,
        callback: Callable[[ActionProgress], None],
    ) -> None:
        """Parse a single pip stderr line and emit action progress if relevant.

        This is extracted as a static method for testability.
        """
        # Downloading <url> (<size>)
        match = _DOWNLOADING_PATTERN.search(line)
        if match:
            url = match.group(1)
            size = match.group(2)
            filename = url.rsplit('/', 1)[-1].split('#')[0]
            callback(
                ActionProgress(
                    action=action,
                    phase='downloading',
                    progress=0.0,
                    message=f'Downloading {filename} ({size})',
                )
            )
            return

        # Lines containing percentage (pip's download bar)
        match = _DOWNLOAD_PROGRESS_PATTERN.search(line)
        if match:
            pct = int(match.group(2))
            callback(
                ActionProgress(
                    action=action,
                    phase='downloading',
                    progress=pct / 100.0,
                    message=None,
                )
            )
            return

        # Installing collected packages: ...
        match = _INSTALLING_PATTERN.search(line)
        if match:
            packages_str = match.group(1).strip()
            callback(
                ActionProgress(
                    action=action,
                    phase='installing',
                    progress=None,
                    message=f'Installing {packages_str}',
                )
            )
            return

        # Requirement already satisfied
        if _ALREADY_SATISFIED_PATTERN.search(line):
            callback(
                ActionProgress(
                    action=action,
                    phase='verifying',
                    progress=1.0,
                    message=line.strip(),
                )
            )
            return

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates using ``pip list --outdated``.

        Uses the native ``pip list --outdated --format=json`` command
        when possible for best compatibility with configured indexes
        and mirrors.  Falls back to the PyPI JSON API if the pip
        module is unavailable.

        When *params.include_prereleases* is ``True``, the ``--pre``
        flag is passed to include pre-release versions.

        Args:
            params: The check parameters including which packages to check.

        Returns:
            A list of packages that have updates available.
        """
        logger = logging.getLogger('porringer.pip.check_updates')

        cmd = [self.python_command(params.runtime_context), '-m', 'pip', 'list', '--outdated', '--format=json']
        if params.include_prereleases:
            cmd.append('--pre')

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                raise RuntimeError('pip list --outdated exited with non-zero status')
            entries: list[dict[str, str]] = json.loads(stdout_bytes.decode('utf-8', errors='replace') or '[]')
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            logger.debug('pip list --outdated failed, falling back to PyPI: %s', exc)
            return await self._check_pypi_updates(params)

        # Filter to requested packages if specified
        requested = {p.name.lower() for p in params.packages} if params.packages else None
        results: list[Package] = []
        for entry in entries:
            name = entry.get('name', '')
            latest = entry.get('latest_version')
            if latest and (requested is None or name.lower() in requested):
                results.append(Package(name=name, version=latest))
        return results

    @override
    async def packages(
        self,
        *,
        project_path: Path | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> list[Package]:
        """Gathers installed packages visible to the active Python.

        When *project_path* is provided, the method discovers the
        project's virtual environment (`<project_path>/.venv`) and
        lists packages from that interpreter.  Otherwise it falls back
        to the runtime context or the system Python on PATH.

        Args:
            project_path: Optional project directory.  When set, the
                listing is scoped to the project's `.venv`.
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use ``sys.executable``.

        Returns:
            A list of packages
        """
        # Determine the effective Python interpreter
        effective_python = self.python_command(runtime_context)
        if project_path is not None:
            venv_python = self._discover_venv_python(project_path)
            if venv_python is not None:
                effective_python = str(venv_python)

        logger = logging.getLogger('porringer.pip.packages')
        logger.debug('listing packages via: %s', effective_python)

        # Try pip list first
        packages = await self._list_packages_via_pip(logger, effective_python)
        if packages is not None:
            return packages

        # pip module is unavailable — fall back to importlib.metadata
        # so that presence detection still works in venvs created
        # without pip (e.g. PDM-managed environments).
        logger.debug('pip module unavailable for %s; falling back to importlib.metadata', effective_python)
        return await self._list_packages_via_importlib(logger, effective_python)

    @staticmethod
    async def _list_packages_via_pip(logger: logging.Logger, python: str = 'python') -> list[Package] | None:
        """List packages using `python -m pip list --format=json`.

        Args:
            logger: Logger instance.
            python: Python interpreter command or path.

        Returns:
            A list of packages, or `None` if pip is not usable.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                python,
                '-m',
                'pip',
                'list',
                '--format=json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError:
            logger.warning('Python not found on PATH; cannot list pip packages')
            return []

        if proc.returncode != 0:
            logger.debug('pip list failed (pip module may not be installed)')
            return None

        try:
            entries: list[dict[str, str]] = json.loads(stdout_bytes.decode('utf-8', errors='replace') or '[]')
            return [
                Package(name=entry['name'], version=entry.get('version'))
                for entry in entries
                if entry.get('name') is not None
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f'Failed to parse pip package list: {e}')
            return []

    @staticmethod
    async def _list_packages_via_importlib(logger: logging.Logger, python: str = 'python') -> list[Package]:
        """List packages using ``importlib.metadata`` via a subprocess.

        This is the fallback for environments where the ``pip`` module
        is not installed (e.g. PDM-managed venvs).  Because
        ``importlib.metadata`` is part of the standard library it is
        always available.

        Args:
            logger: Logger instance.
            python: Python interpreter command or path.

        Returns:
            A list of packages (empty on failure).
        """
        script = (
            'import importlib.metadata, json, sys; '
            'json.dump('
            '[{"name": d.name, "version": d.version} '
            'for d in importlib.metadata.distributions()], '
            'sys.stdout)'
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                python,
                '-c',
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError:
            logger.warning('Python not found on PATH; cannot list packages via importlib.metadata')
            return []

        if proc.returncode != 0:
            logger.debug('importlib.metadata fallback failed (returncode=%s)', proc.returncode)
            return []

        try:
            entries: list[dict[str, str]] = json.loads(stdout_bytes.decode('utf-8', errors='replace') or '[]')
            return [
                Package(name=entry['name'], version=entry.get('version'))
                for entry in entries
                if entry.get('name') is not None
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning('Failed to parse importlib.metadata package list: %s', e)
            return []
