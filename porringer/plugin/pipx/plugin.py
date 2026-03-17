"""Plugin implementation"""

import asyncio
import json
import os
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.plugin_manager import find_tool_python
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import (
    Package,
    PackageRef,
    PackageRelation,
    PackageRelationKind,
    PluginKind,
)


def _get_pipx_venvs_dir() -> Path:
    """Get the pipx venvs directory.

    Checks PIPX_HOME environment variable first, then falls back to
    the default pipx location (~/.local/pipx on Linux/macOS, ~/pipx on Windows).

    Returns:
        Path to the pipx venvs directory.
    """
    pipx_home = os.environ.get("PIPX_HOME")
    if pipx_home:
        return Path(pipx_home) / "venvs"

    # Default pipx home location (not platformdirs)
    # On Windows: ~/pipx, on Unix: ~/.local/pipx
    if os.name == "nt":
        return Path.home() / "pipx" / "venvs"
    else:
        return Path.home() / ".local" / "pipx" / "venvs"


def _read_venv_packages_sync(venv_dir: Path) -> list[Package]:
    """Read packages from a single pipx venv directory (synchronous).

    Parses ``pipx_metadata.json`` and returns both the main package
    and any injected packages.  Injected packages carry a
    :class:`PackageRelation` linking them to the host tool.

    Args:
        venv_dir: Path to a pipx venv directory.

    Returns:
        A list of packages found in the venv.
    """
    metadata_file = venv_dir / "pipx_metadata.json"
    if not metadata_file.exists():
        return []

    try:
        metadata = json.loads(metadata_file.read_text())
    except json.JSONDecodeError, KeyError:
        return []

    packages: list[Package] = []
    main_package = metadata.get("main_package", {})
    name = main_package.get("package")
    version = main_package.get("package_version")
    if name:
        packages.append(Package(name=name, version=version))

        # Injected packages carry a relation back to the host tool
        for _key, injected in metadata.get("injected_packages", {}).items():
            inj_name = injected.get("package")
            inj_version = injected.get("package_version")
            if inj_name:
                packages.append(
                    Package(
                        name=inj_name,
                        version=inj_version,
                        relation=PackageRelation(
                            host=name, kind=PackageRelationKind.INJECTED
                        ),
                    )
                )

    return packages


class PIPXEnvironment(PythonEnvironment):
    """Represents a Python environment managed by pipx.

    Provides methods to install, search, uninstall, upgrade, and list Python packages using
    pipx as the backend package manager.

    This plugin can optionally use a Python runtime provider (like pim) for managing
    the underlying Python installation.
    """

    @staticmethod
    @override
    def plugin_kind() -> PluginKind:
        """Pipx installs CLI tools in isolated environments."""
        return PluginKind.TOOL

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pipx wraps the `pipx` CLI."""
        return "pipx"

    @classmethod
    @override
    def is_available_for(cls, runtime_context: RuntimeContext) -> bool:
        """Pipx is a standalone binary — availability is PATH-based only.

        Unlike ``pip`` (which runs as ``python -m pip``), pipx is
        always invoked directly.  A resolved Python runtime does not
        affect whether pipx itself is installed.
        """
        return cls.is_available()

    @override
    def package_python(self, package_name: str) -> str | None:
        """Return the Python interpreter from a package's own pipx venv.

        Each pipx-installed tool lives in its own isolated venv.  To
        introspect a package's extras we must query *that* venv's
        Python, not the calling process's ``sys.executable``.

        Uses :func:`find_tool_python` to locate the interpreter by
        inspecting the tool's executable on PATH.

        Args:
            package_name: The name of the pipx-installed tool.

        Returns:
            Path to the tool's venv Python, or ``None`` when it
            cannot be determined.
        """
        return find_tool_python(package_name)

    @override
    def install_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command to install a package via pipx."""
        cmd = ["pipx", "install", package.specifier]
        if include_prereleases:
            cmd.extend(["--pip-args=--pre"])
        return cmd

    @override
    def upgrade_command(
        self,
        package: PackageRef,
        *,
        include_prereleases: bool = False,
        runtime_context: RuntimeContext | None = None,
    ) -> list[str]:
        """Returns the CLI command to upgrade a package via pipx."""
        cmd = ["pipx", "upgrade", package.specifier]
        if include_prereleases:
            cmd.extend(["--pip-args=--pre"])
        return cmd

    @override
    def uninstall_command(
        self, package: PackageRef, *, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to uninstall a package via pipx."""
        return ["pipx", "uninstall", package.name]

    @override
    async def packages(
        self,
        *,
        project_path: Path | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> list[Package]:
        """Gathers installed packages in the given environment.

        pipx manages isolated CLI tool installations globally, so
        *project_path* is accepted for interface compatibility but
        has no effect on the result.

        Per-venv metadata reads are offloaded to threads and run in
        parallel so the event loop is never blocked, even when many
        venvs exist.

        Args:
            project_path: Unused.  pipx is inherently global.
            runtime_context: Unused.  pipx manages isolated venvs
                independently of the active interpreter.

        Returns:
            A list of packages
        """
        pipx_venvs = _get_pipx_venvs_dir()

        if not pipx_venvs.exists():
            return []

        venv_dirs = [d for d in pipx_venvs.iterdir() if d.is_dir()]
        if not venv_dirs:
            return []

        # Fan out per-venv reads in parallel, each offloaded to a thread
        results = await asyncio.gather(
            *[asyncio.to_thread(_read_venv_packages_sync, d) for d in venv_dirs]
        )

        # Flatten the per-venv lists into a single list
        packages: list[Package] = []
        for venv_packages in results:
            packages.extend(venv_packages)

        return packages
