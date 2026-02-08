"""Plugin implementation for pyenv-managed Python runtimes."""

import logging
import subprocess
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Package, PackageRef


class PyenvEnvironment(Environment, RuntimeProvider):
    """Manages Python runtimes via pyenv (Linux / macOS).

    Uses ``pyenv`` to install, list, and manage Python interpreter
    versions.  Implements :class:`RuntimeProvider` so the sync engine
    can resolve the filesystem path to a managed interpreter.

    CLI Reference:
        - pyenv versions --bare           — list installed versions
        - pyenv install [-s] <version>    — install a version
        - pyenv uninstall -f <version>    — remove a version
        - pyenv prefix <version>          — print install prefix
    """

    @staticmethod
    @override
    def package_backend() -> str:
        """Pyenv manages the ``python-runtime`` package backend."""
        return 'python-runtime'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pyenv wraps the ``pyenv`` CLI."""
        return 'pyenv'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a Python runtime via pyenv."""
        return ['pyenv', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade (reinstall) a Python runtime via pyenv."""
        return ['pyenv', 'install', '--skip-existing', package.name]

    # ------------------------------------------------------------------
    # RuntimeProvider
    # ------------------------------------------------------------------

    def resolve_executable(self, tag: str) -> Path | None:
        """Return the path to the Python interpreter for a pyenv-managed runtime.

        Uses ``pyenv prefix <tag>`` to find the install directory, then
        appends ``bin/python``.

        Args:
            tag: The Python version (e.g. ``"3.14.0"``, ``"3.12.4"``).

        Returns:
            Absolute path to the interpreter, or ``None`` if not installed.
        """
        logger = logging.getLogger('porringer.pyenv.resolve_executable')
        try:
            result = subprocess.run(
                ['pyenv', 'prefix', tag],
                capture_output=True,
                text=True,
                check=True,
            )
            prefix = Path(result.stdout.strip())
            executable = prefix / 'bin' / 'python'
            if executable.exists():
                return executable
            logger.warning('pyenv prefix %s resolved to %s but bin/python missing', tag, prefix)
        except subprocess.CalledProcessError as e:
            logger.debug('pyenv prefix %s failed: %s', tag, e.stderr.strip() if e.stderr else e)
        except FileNotFoundError:
            logger.debug('pyenv not found on PATH')
        except Exception as e:
            logger.debug('resolve_executable failed for tag %s: %s', tag, e)
        return None

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs a Python runtime using pyenv.

        Args:
            params: Installation parameters; ``package.name`` is the version
                    string (e.g. ``"3.14.0"``).

        Returns:
            The installed package, or ``None`` on failure.
        """
        logger = logging.getLogger('porringer.pyenv.install')
        version = params.package.name

        if params.dry:
            logger.info('[dry-run] Would run: pyenv install %s', version)
            return Package(name=params.package.name, version=version)

        try:
            result = subprocess.run(
                ['pyenv', 'install', '-s', version],
                capture_output=True,
                text=True,
                check=False,
            )
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pyenv not found. See https://github.com/pyenv/pyenv#installation')
            return None
        except Exception as e:
            logger.error('Failed to install Python %s via pyenv: %s', version, e)
            return None

        return Package(name=params.package.name, version=version)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches for an available Python version via ``pyenv install --list``.

        Args:
            package: The version reference to search for.

        Returns:
            The package if a matching version is found, or ``None``.
        """
        logger = logging.getLogger('porringer.pyenv.search')
        target = package.name.strip()

        try:
            result = subprocess.run(
                ['pyenv', 'install', '--list'],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None

            for line in result.stdout.splitlines():
                candidate = line.strip()
                if candidate == target or candidate.startswith(f'{target}.'):
                    return Package(name=package.name, version=candidate)
        except FileNotFoundError:
            logger.error('pyenv not found')
        except Exception as e:
            logger.error('Failed to search for Python %s via pyenv: %s', target, e)

        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls Python runtimes via ``pyenv uninstall -f``.

        Args:
            params: Uninstall parameters.

        Returns:
            List of uninstalled packages (``None`` for failures).
        """
        logger = logging.getLogger('porringer.pyenv.uninstall')
        results: list[Package | None] = []

        for pkg in params.packages:
            version = pkg.name

            if params.dry:
                logger.info('[dry-run] Would run: pyenv uninstall -f %s', version)
                results.append(Package(name=pkg.name, version=version))
                continue

            try:
                result = subprocess.run(
                    ['pyenv', 'uninstall', '-f', version],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=version))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('pyenv not found')
                results.append(None)
            except Exception as e:
                logger.error('Failed to uninstall Python %s via pyenv: %s', version, e)
                results.append(None)

        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades (reinstalls) a Python runtime via pyenv.

        Pyenv doesn't have a native upgrade; we reinstall with ``-s`` to
        skip if already the latest patch.

        Args:
            params: Upgrade parameters.

        Returns:
            The upgraded package, or ``None`` on failure.
        """
        logger = logging.getLogger('porringer.pyenv.upgrade')
        version = params.package.name

        if params.dry:
            logger.info('[dry-run] Would run: pyenv install -s %s', version)
            return Package(name=params.package.name, version=version)

        try:
            result = subprocess.run(
                ['pyenv', 'install', '-s', version],
                capture_output=True,
                text=True,
                check=False,
            )
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('pyenv not found')
            return None
        except Exception as e:
            logger.error('Failed to upgrade Python %s via pyenv: %s', version, e)
            return None

        return Package(name=params.package.name, version=version)

    @override
    def packages(self) -> list[Package]:
        """Lists installed Python runtimes via ``pyenv versions --bare``.

        Returns:
            A list of installed Python runtime packages.
        """
        logger = logging.getLogger('porringer.pyenv.packages')
        packages: list[Package] = []

        try:
            result = subprocess.run(
                ['pyenv', 'versions', '--bare'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('pyenv versions --bare failed')
                return packages

            for line in result.stdout.splitlines():
                version = line.strip()
                if version:
                    packages.append(Package(name=version, version=version))

        except FileNotFoundError:
            logger.error('pyenv not found on PATH')
        except Exception as e:
            logger.error('Failed to list pyenv runtimes: %s', e)

        return packages
