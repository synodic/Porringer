"""Plugin implementation for Python Install Manager (pymanager)"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginDependency, PluginKind


class PimEnvironment(Environment, RuntimeProvider):
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
    def resolve_executable(self, tag: str) -> Path | None:
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
            result = subprocess.run(
                ['py', f'-{tag}', '-c', 'import sys; print(sys.executable)'],
                capture_output=True,
                text=True,
                check=True,
            )
            path = Path(result.stdout.strip())
            if path.exists():
                return path
            logger.warning('py -%s resolved to %s but it does not exist', tag, path)
        except subprocess.CalledProcessError as e:
            logger.debug('py -%s failed: %s', tag, e.stderr.strip() if e.stderr else e)
        except FileNotFoundError:
            logger.debug('py launcher not found')
        except Exception as e:
            logger.debug('resolve_executable failed for tag %s: %s', tag, e)
        return None

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a Python runtime via pymanager."""
        return ['py', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a Python runtime via pymanager."""
        return ['py', 'install', '--update', package.name]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Lists all installed Python runtimes.

        pim manages Python runtimes globally; *project_path* is
        accepted for interface compatibility but has no effect.

        Args:
            project_path: Unused.  pim is inherently global.

        Returns:
            A list of installed Python runtime packages
        """
        logger = logging.getLogger('porringer.pim.packages')
        packages: list[Package] = []

        try:
            # List installed runtimes in JSON format, only those managed by pymanager
            result = subprocess.run(
                ['py', 'list', '--only-managed', '-f', 'json'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed Python runtimes')
                return packages

            # Parse JSON output - format is {"versions": [...]}
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            runtimes = data.get('versions', []) if isinstance(data, dict) else []
            for runtime in runtimes:
                tag = runtime.get('tag', 'unknown')
                version = runtime.get('sort-version') or tag
                # Use tag as the package name since that's how users identify runtimes
                packages.append(
                    Package(
                        name=tag,
                        version=version,
                    )
                )

        except FileNotFoundError:
            logger.error('Python Install Manager (py) not found')
        except json.JSONDecodeError:
            logger.warning('Could not parse installed runtimes list')
        except Exception as e:
            logger.error(f'Failed to list Python runtimes: {e}')

        return packages

    @staticmethod
    def _get_runtime_version(tag: str) -> str | None:
        """Gets the actual version string for an installed runtime.

        Args:
            tag: The Python version tag (e.g., "3.12")

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['py', 'list', '--only-managed', '-f', 'json', tag],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                runtimes = data.get('versions', []) if isinstance(data, dict) else []
                if runtimes:
                    return runtimes[0].get('sort-version') or runtimes[0].get('tag')
        except Exception:
            pass

        return None
