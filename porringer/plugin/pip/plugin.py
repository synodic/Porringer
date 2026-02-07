"""Plugin implementation"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef, PluginParameters
from porringer.schema import SetupAction, SetupActionType, SubActionProgress
from porringer.utility.utility import async_run_command

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


class PipEnvironment(Environment):
    """Represents a Python environment managed by pip.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using pip
    as the backend package manager.

    This plugin can optionally use a Python runtime provider (like pim) for managing
    the underlying Python installation.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the pip environment plugin.

        Args:
            parameters: Plugin parameters including distribution info
        """
        super().__init__(parameters)
        self._cached_packages: list[Package] | None = None

    @staticmethod
    @override
    def package_backend() -> str:
        """Pip manages the ``python`` package backend."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pip wraps the ``pip`` CLI."""
        return 'pip'

    @classmethod
    @override
    def is_available(cls) -> bool:
        """Checks if pip is usable.

        Pip can be invoked either as a standalone ``pip`` executable or
        via ``python -m pip``.  A standalone ``pip`` may be absent in
        uv-created virtual environments even though ``python -m pip``
        works perfectly.  This override accepts either form so that the
        plugin is not incorrectly reported as unavailable.

        Returns:
            True if ``pip`` or ``python`` is found on PATH, False otherwise.
        """
        return shutil.which('pip') is not None or shutil.which('python') is not None

    @staticmethod
    @override
    def install_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via pip."""
        return ['pip', 'install', package.specifier]

    @staticmethod
    @override
    def upgrade_command(package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via pip."""
        return ['pip', 'install', '--upgrade', package.specifier]

    @staticmethod
    @override
    def supports_parallel() -> bool:
        """Pip does not support parallel installs safely due to potential conflicts."""
        return False

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name using pip."""
        logger = logging.getLogger('porringer.pip.install')
        args = ['python', '-m', 'pip', 'install', params.package.specifier]
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('Python not found. Install Python from https://python.org')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
            return None
        return Package(name=params.package.name, version=None)

    @override
    async def async_install(self, params: PackageParameters) -> Package | None:
        """Asynchronously installs the given package using pip.

        When a progress_callback is provided, streams stderr line-by-line to
        report download and install phases. Otherwise falls back to the simple
        ``async_run_command`` path for zero overhead.
        """
        logger = logging.getLogger('porringer.pip.install')
        args = ['python', '-m', 'pip', 'install', params.package.specifier]
        if params.dry:
            args.append('--dry-run')

        if params.progress_callback is None:
            # Fast path — no streaming needed
            return await self._async_install_simple(args, params.package, logger)

        return await self._async_install_with_progress(args, params, logger)

    @staticmethod
    async def _async_install_simple(args: list[str], package: PackageRef, logger: logging.Logger) -> Package | None:
        """Install without progress streaming."""
        try:
            result = await async_run_command(args)
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
    async def _async_install_with_progress(
        args: list[str],
        params: PackageParameters,
        logger: logging.Logger,
    ) -> Package | None:
        """Install with line-by-line stderr streaming for progress reporting."""
        assert params.progress_callback is not None  # guaranteed by caller

        action = SetupAction(
            action_type=SetupActionType.PACKAGE,
            description=f'Install {params.package.specifier}',
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
                PipEnvironment._parse_progress_line(line, action, params.progress_callback)

        try:
            await asyncio.gather(read_stdout(), read_stderr_lines())
            await process.wait()
        except Exception as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
            return None

        logger.info(stdout_data.decode('utf-8', errors='replace'))

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
    def search(self, package: PackageRef) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            package: The package reference to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using pip."""
        logger = logging.getLogger('porringer.pip.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['python', '-m', 'pip', 'uninstall', '-y', pkg.name]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('Python not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {pkg.name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades the given package using pip."""
        logger = logging.getLogger('porringer.pip.upgrade')
        pkg = params.package
        args = ['python', '-m', 'pip', 'install', '--upgrade', pkg.specifier]
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('Python not found')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to upgrade {pkg.name}: {e}')
            return None
        return Package(name=pkg.name, version=None)

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages visible to the active Python on PATH.

        Uses ``python -m pip list --format=json`` via subprocess so that the
        result reflects the *user's* active environment (e.g. a project venv)
        rather than the interpreter that porringer itself runs under.

        The result is cached for the lifetime of this plugin instance to avoid
        repeated subprocess invocations (``packages()`` is called once per
        package action).

        Returns:
            A list of packages
        """
        if self._cached_packages is not None:
            return self._cached_packages

        logger = logging.getLogger('porringer.pip.packages')
        try:
            result = subprocess.run(
                ['python', '-m', 'pip', 'list', '--format=json'],
                capture_output=True,
                text=True,
                check=True,
            )
            entries: list[dict[str, str]] = json.loads(result.stdout)
            self._cached_packages = [
                Package(name=entry['name'], version=entry.get('version'))
                for entry in entries
                if entry.get('name') is not None
            ]
        except subprocess.CalledProcessError as e:
            logger.warning(f'Failed to list pip packages: {e}')
            self._cached_packages = []
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f'Failed to parse pip package list: {e}')
            self._cached_packages = []
        except FileNotFoundError:
            logger.warning('Python not found on PATH; cannot list pip packages')
            self._cached_packages = []

        return self._cached_packages
