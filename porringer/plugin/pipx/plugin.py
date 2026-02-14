"""Plugin implementation"""

import json
import os
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind


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
    def ecosystem() -> Ecosystem:
        """Pipx belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @staticmethod
    @override
    def plugin_kind() -> PluginKind:
        """Pipx installs CLI tools in isolated environments."""
        return PluginKind.TOOL

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
        """Pipx wraps the `pipx` CLI."""
        return 'pipx'

    @staticmethod
    @override
    def supports_injection() -> bool:
        """Pipx supports injection via ``pipx inject``."""
        return True

    @override
    def inject_command(self, target: PackageRef, plugin: PackageRef) -> list[str]:
        """Returns the CLI command to inject a sub-package into a pipx venv."""
        return ['pipx', 'inject', target.name, plugin.specifier]

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via pipx."""
        return ['pipx', 'install', package.specifier]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via pipx."""
        return ['pipx', 'upgrade', package.specifier]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        """Gathers installed packages in the given environment.

        pipx manages isolated CLI tool installations globally, so
        *project_path* is accepted for interface compatibility but
        has no effect on the result.

        Args:
            project_path: Unused.  pipx is inherently global.

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
                    # Also report injected packages
                    for _key, injected in metadata.get('injected_packages', {}).items():
                        inj_name = injected.get('package')
                        inj_version = injected.get('package_version')
                        if inj_name:
                            packages.append(Package(name=inj_name, version=inj_version))
                except json.JSONDecodeError, KeyError:
                    continue

        return packages
