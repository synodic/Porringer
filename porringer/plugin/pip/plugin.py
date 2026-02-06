"""Plugin implementation"""

import asyncio
import logging
import re
import subprocess
from collections.abc import Callable
from importlib.metadata import distributions
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    InstallParameters,
    ProviderRequirement,
    UninstallParameters,
    UpgradeParameters,
)
from porringer.core.schema import Package, PackageName
from porringer.schema import SetupAction, SetupActionType, SubActionProgress
from porringer.utility.utility import async_run_command

# Capability identifier for Python runtime providers
PYTHON_RUNTIME_CAPABILITY = 'python-runtime'

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

    @staticmethod
    @override
    def requires_providers() -> list[ProviderRequirement]:
        """Declares that pip can optionally use a Python runtime provider.

        The provider is optional - pip can also work with system-installed Python.
        Returns a list of platform-specific providers that the builder can select from:
        - pim: Python Install Manager (Windows)
        - brew: Homebrew (macOS)
        - apt: APT package manager (Linux)

        NOTE: Currently defaults to using the latest available Python from the provider.
        Future versions may support configuration for selecting specific versions.

        Returns:
            A list of provider requirements for each supported platform
        """
        return [
            # Windows: Python Install Manager
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='pim',
            ),
            # macOS: Homebrew
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='brew',
            ),
            # Linux: APT
            ProviderRequirement(
                capability=PYTHON_RUNTIME_CAPABILITY,
                required=False,
                provider_plugin='apt',
            ),
        ]

    @staticmethod
    @override
    def install_command(package: PackageName) -> list[str]:
        """Returns the CLI command to install a package via pip."""
        return ['pip', 'install', str(package)]

    @staticmethod
    @override
    def supports_parallel() -> bool:
        """Pip does not support parallel installs safely due to potential conflicts."""
        return False

    @override
    def install(self, params: InstallParameters) -> Package | None:
        """Installs the given package identified by its name using pip."""
        logger = logging.getLogger('porringer.pip.install')
        args = ['python', '-m', 'pip', 'install', str(params.name)]
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
            logger.error(f'Failed to install {params.name}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {params.name}: {e}')
            return None
        return Package(name=params.name, version=None)

    @override
    async def async_install(self, params: InstallParameters) -> Package | None:
        """Asynchronously installs the given package using pip.

        When a progress_callback is provided, streams stderr line-by-line to
        report download and install phases. Otherwise falls back to the simple
        ``async_run_command`` path for zero overhead.
        """
        logger = logging.getLogger('porringer.pip.install')
        args = ['python', '-m', 'pip', 'install', str(params.name)]
        if params.dry:
            args.append('--dry-run')

        if params.progress_callback is None:
            # Fast path — no streaming needed
            return await self._async_install_simple(args, params.name, logger)

        return await self._async_install_with_progress(args, params, logger)

    @staticmethod
    async def _async_install_simple(args: list[str], name: PackageName, logger: logging.Logger) -> Package | None:
        """Install without progress streaming."""
        try:
            result = await async_run_command(args)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except TimeoutError:
            logger.error(f'Timeout installing {name}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {name}: {e}')
            return None
        return Package(name=name, version=None)

    @staticmethod
    async def _async_install_with_progress(
        args: list[str],
        params: InstallParameters,
        logger: logging.Logger,
    ) -> Package | None:
        """Install with line-by-line stderr streaming for progress reporting."""
        assert params.progress_callback is not None  # guaranteed by caller

        action = SetupAction(
            action_type=SetupActionType.INSTALL_PACKAGE,
            description=f'Install {params.name}',
            plugin='pip',
            package=str(params.name),
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error(f'Python not found while installing {params.name}')
            return None

        # Report initial phase
        params.progress_callback(
            SubActionProgress(
                action=action,
                phase='resolving',
                progress=None,
                message=f'Resolving {params.name}...',
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
            logger.error(f'Failed to install {params.name}: {e}')
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
                message=f'Installed {params.name}',
            )
        )

        return Package(name=params.name, version=None)

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
    def search(self, name: PackageName) -> Package | None:
        """Searches the environment's sources for a package

        Args:
            name: The package name to search for

        Returns:
            The package, or None if it doesn't exist
        """
        raise NotImplementedError

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls the given list of packages using pip."""
        logger = logging.getLogger('porringer.pip.uninstall')
        results: list[Package | None] = []
        for name in params.names:
            args = ['python', '-m', 'pip', 'uninstall', '-y', str(name)]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('Python not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to uninstall {name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {name}: {e}')
                results.append(None)
        return results

    @override
    def upgrade(self, params: UpgradeParameters) -> list[Package | None]:
        """Upgrades the given list of packages using pip."""
        logger = logging.getLogger('porringer.pip.upgrade')
        results: list[Package | None] = []
        for name in params.names:
            args = ['python', '-m', 'pip', 'install', '--upgrade', str(name)]
            if params.dry:
                args.append('--dry-run')
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('Python not found')
                results.append(None)
            except subprocess.SubprocessError as e:
                logger.error(f'Failed to upgrade {name}: {e}')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to upgrade {name}: {e}')
                results.append(None)
        return results

    @override
    def packages(self) -> list[Package]:
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        return [
            Package(name=PackageName(dist.metadata['Name']), version=dist.version)
            for dist in distributions()
            if dist.metadata['Name'] is not None
        ]
