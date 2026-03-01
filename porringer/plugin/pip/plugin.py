"""Plugin implementation"""

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    CheckUpdatesParameters,
    PackageParameters,
)
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import SetupAction, SubActionProgress
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
    def tool_name(cls) -> str:
        """Pip wraps the `pip` CLI."""
        return 'pip'

    @classmethod
    @override
    def is_available(cls) -> bool:
        """Checks if pip is usable.

        Requires the ``pip`` executable to be on PATH.  Environments
        that only have ``python`` (e.g. uv-created virtual
        environments without pip) should use the ``uv`` plugin
        instead.

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
    async def install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package using pip.

        When a progress_callback is provided, streams stderr line-by-line to
        report download and install phases. Otherwise falls back to the simple
        `run_command` path for zero overhead.
        """
        logger = logging.getLogger('porringer.pip.install')
        args = list(
            self.install_command(
                params.package, include_prereleases=params.include_prereleases, runtime_context=params.runtime_context
            )
        )
        if params.dry:
            args.append('--dry-run')

        if params.progress_callback is None:
            # Fast path — no streaming needed
            return await self._install_simple(args, params.package, logger)

        return await self._install_with_progress(args, params, logger)

    @staticmethod
    async def _install_simple(args: list[str], package: PackageRef, logger: logging.Logger) -> Package | None:
        """Install without progress streaming."""
        try:
            result = await run_command(args)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except TimeoutError:
            logger.error(f'Timeout installing {package.name}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {package.name}: {e}')
            return None
        return Package(name=package.name, version=None)

    @staticmethod
    async def _install_with_progress(
        args: list[str],
        params: PackageParameters,
        logger: logging.Logger,
    ) -> Package | None:
        """Install with line-by-line stderr streaming for progress reporting."""
        assert params.progress_callback is not None  # guaranteed by caller

        action = SetupAction(
            description=f'Install {params.package.specifier}',
            kind=PluginKind.PACKAGE,
            installer='pip',
            package=params.package,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error(f'Python not found while installing {params.package.name}')
            return None

        # Report initial phase
        params.progress_callback(
            SubActionProgress(
                action=action,
                phase='resolving',
                progress=None,
                message=f'Resolving {params.package.specifier}...',
            )
        )

        stderr_lines: list[str] = []
        stdout_data = b''

        async def read_stdout() -> None:
            nonlocal stdout_data
            assert process.stdout is not None
            stdout_data = await process.stdout.read()

        async def read_stderr_lines() -> None:
            assert process.stderr is not None
            assert params.progress_callback is not None
            async for raw_line in process.stderr:
                line = raw_line.decode('utf-8', errors='replace').rstrip()
                stderr_lines.append(line)

                # Emit raw output line for log panel display
                params.progress_callback(
                    SubActionProgress(
                        action=action,
                        phase='running',
                        output=line,
                        stream='stderr',
                    )
                )

                # Also emit parsed progress events for structured updates
                PIPEnvironment._parse_progress_line(line, action, params.progress_callback)

        try:
            await asyncio.gather(read_stdout(), read_stderr_lines())
            await process.wait()
        except Exception as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
            return None

        # Emit stdout lines as output events
        stdout_text = stdout_data.decode('utf-8', errors='replace')
        logger.info(stdout_text)
        for line in stdout_text.splitlines():
            stripped = line.rstrip()
            if stripped:
                params.progress_callback(
                    SubActionProgress(
                        action=action,
                        phase='running',
                        output=stripped,
                        stream='stdout',
                    )
                )

        if process.returncode != 0:
            logger.error('\n'.join(stderr_lines))
            return None

        # Report completion
        params.progress_callback(
            SubActionProgress(
                action=action,
                phase='done',
                progress=1.0,
                message=f'Installed {params.package.name}',
            )
        )

        return Package(name=params.package.name, version=None)

    @staticmethod
    def _parse_progress_line(
        line: str,
        action: SetupAction,
        callback: Callable[[SubActionProgress], None],
    ) -> None:
        """Parse a single pip stderr line and emit sub-action progress if relevant.

        This is extracted as a static method for testability.
        """
        # Downloading <url> (<size>)
        match = _DOWNLOADING_PATTERN.search(line)
        if match:
            url = match.group(1)
            size = match.group(2)
            filename = url.rsplit('/', 1)[-1].split('#')[0]
            callback(
                SubActionProgress(
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
                SubActionProgress(
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
                SubActionProgress(
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
                SubActionProgress(
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

        cmd = [self.python_command(), '-m', 'pip', 'list', '--outdated', '--format=json']
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
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            entries: list[dict[str, str]] = json.loads(stdout)
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

        # pip module is unavailable — the pip plugin cannot operate
        # (install, upgrade, or uninstall) without it.
        logger.warning('pip module unavailable for %s; returning empty package list', effective_python)
        return []

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
            if proc.returncode != 0:
                logger.debug('pip list failed (pip module may not be installed)')
                return None
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            entries: list[dict[str, str]] = json.loads(stdout)
            return [
                Package(name=entry['name'], version=entry.get('version'))
                for entry in entries
                if entry.get('name') is not None
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f'Failed to parse pip package list: {e}')
            return []
        except FileNotFoundError:
            logger.warning('Python not found on PATH; cannot list pip packages')
            return []
