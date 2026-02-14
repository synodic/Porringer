"""Plugin implementation for pyenv-managed Python runtimes."""

import logging
import subprocess
import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeProvider
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

    @override
    def resolve_executable(self, tag: str) -> Path | None:
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
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Lists installed Python runtimes via ``pyenv versions --bare``.

        pyenv manages Python runtimes globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  pyenv is inherently global.

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
