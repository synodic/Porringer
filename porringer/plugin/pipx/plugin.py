"""Plugin implementation"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.utility.utility import async_run_command


def _get_pipx_venvs_dir() -> Path:
    """Get the pipx venvs directory.

    Checks PIPX_HOME environment variable first, then falls back to
    the default pipx location (~/.local/pipx on Linux/macOS, ~/pipx on Windows).

    Returns:
        Path to the pipx venvs directory.
    """
    pipx_home = os.environ.get('PIPX_HOME')
    if pipx_home:
        return Path(pipx_home) / 'venvs'

    # Default pipx home location (not platformdirs)
    # On Windows: ~/pipx, on Unix: ~/.local/pipx
    if os.name == 'nt':
        return Path.home() / 'pipx' / 'venvs'
    else:
        return Path.home() / '.local' / 'pipx' / 'venvs'


class PipxEnvironment(Environment, RuntimeConsumer):
    """Represents a Python environment managed by pipx.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using
    pipx as the backend package manager.

    This plugin can optionally use a Python runtime provider (like pim) for managing
    the underlying Python installation.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """Pipx belongs to the ``python`` ecosystem."""
        return 'python'

    @staticmethod
    @override
    def plugin_kind() -> PluginKind:
        """Pipx installs CLI tools in isolated environments."""
        return PluginKind.TOOL

    @staticmethod
    @override
    def default_priority() -> int:
        """Pipx is the preferred Python tool installer (priority 10)."""
        return 10

    @staticmethod
    @override
    def package_name_validator() -> str:
        """Python packages use PEP 440 validation."""
        return 'pep440'

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Pipx consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pipx wraps the ``pipx`` CLI."""
        return 'pipx'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via pipx."""
        return ['pipx', 'install', package.specifier]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via pipx."""
        return ['pipx', 'upgrade', package.specifier]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name using pipx."""
        logger = logging.getLogger('porringer.pipx.install')
        args = ['pipx', 'install', params.package.specifier]
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pipx not found. Install it from https://pipx.pypa.io')
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
        """Asynchronously installs the given package using pipx."""
        logger = logging.getLogger('porringer.pipx.install')
        args = ['pipx', 'install', params.package.specifier]
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=None)
        try:
            result = await async_run_command(args)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except TimeoutError:
            logger.error(f'Timeout installing {params.package.name}')
            return None
        except Exception as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
            return None
        return Package(name=params.package.name, version=None)

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
        """Uninstalls the given list of packages using pipx."""
        logger = logging.getLogger('porringer.pipx.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['pipx', 'uninstall', pkg.name]
            if params.dry:
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=pkg.name, version=None))
                continue
            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('pipx not found')
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
        """Upgrades the given package using pipx."""
        logger = logging.getLogger('porringer.pipx.upgrade')
        pkg = params.package
        args = ['pipx', 'upgrade', pkg.specifier]
        if params.dry:
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pipx not found')
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
        """Gathers installed packages in the given environment

        Returns:
            A list of packages
        """
        packages: list[Package] = []
        pipx_venvs = _get_pipx_venvs_dir()

        if not pipx_venvs.exists():
            return packages

        for venv_dir in pipx_venvs.iterdir():
            metadata_file = venv_dir / 'pipx_metadata.json'
            if metadata_file.exists():
                try:
                    metadata = json.loads(metadata_file.read_text())
                    main_package = metadata.get('main_package', {})
                    name = main_package.get('package')
                    version = main_package.get('package_version')
                    if name:
                        packages.append(Package(name=name, version=version))
                except json.JSONDecodeError, KeyError:
                    continue

        return packages
