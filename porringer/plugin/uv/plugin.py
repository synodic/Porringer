"""Plugin implementation"""

import json
import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef, PluginParameters


class UvEnvironment(Environment):
    """Represents a Python environment managed by uv.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using uv
    as the backend package manager.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the uv environment plugin."""
        super().__init__(parameters)
        self._cached_packages: list[Package] | None = None

    def _python_args(self) -> list[str]:
        """Return ``['--python', '<path>']`` when an override is active.

        Reads from ``self.python_executable`` (set by the sync engine
        via a runtime provider).  Returns an empty list when no override
        is set.
        """
        if self.python_executable is not None:
            return ['--python', str(self.python_executable)]
        return []

    @staticmethod
    @override
    def package_backend() -> str:
        """UV manages the ``python`` package backend."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV wraps the ``uv`` CLI."""
        return 'uv'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via uv."""
        return ['uv', 'pip', 'install', *self._python_args(), package.specifier]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via uv."""
        return ['uv', 'pip', 'install', '--upgrade', *self._python_args(), package.specifier]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs the given package identified by its name using uv."""
        logger = logging.getLogger('porringer.uv.install')
        args = list(self.install_command(params.package))
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('uv not found. Install it from https://docs.astral.sh/uv')
            return None
        except subprocess.SubprocessError as e:
            logger.error(f'Failed to install {params.package.name}: {e}')
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
        """Uninstalls the given list of packages using uv."""
        logger = logging.getLogger('porringer.uv.uninstall')
        results: list[Package | None] = []
        for pkg in params.packages:
            args = ['uv', 'pip', 'uninstall', pkg.name]
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
                logger.error('uv not found')
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
        """Upgrades the given package using uv."""
        logger = logging.getLogger('porringer.uv.upgrade')
        pkg = params.package
        args = list(self.upgrade_command(pkg))
        if params.dry:
            args.append('--dry-run')
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('uv not found')
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
        """Gathers installed packages using ``uv pip list --format=json``.

        Results are cached per-instance so multiple calls within a single
        sync run don't shell out repeatedly.

        Returns:
            A list of installed packages.
        """
        if self._cached_packages is not None:
            return self._cached_packages

        logger = logging.getLogger('porringer.uv.packages')
        try:
            args = ['uv', 'pip', 'list', '--format=json', *self._python_args()]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.error('uv pip list failed: %s', result.stderr)
                self._cached_packages = []
                return self._cached_packages

            entries: list[dict[str, str]] = json.loads(result.stdout)
            self._cached_packages = [Package(name=entry['name'], version=entry.get('version')) for entry in entries]
        except FileNotFoundError:
            logger.error('uv not found on PATH')
            self._cached_packages = []
        except (json.JSONDecodeError, subprocess.SubprocessError) as e:
            logger.error('Failed to list uv packages: %s', e)
            self._cached_packages = []

        return self._cached_packages
