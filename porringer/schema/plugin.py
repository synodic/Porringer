"""Data models and schemas for plugin."""

"""Plugin metadata schemas."""

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from packaging.version import Version

from porringer.core.schema import Package, PluginKind


class PluginCapability(Enum):
    """Capabilities a plugin may implement via protocol mixins.

    Used by :meth:`DiscoveredPlugins.capabilities` and
    :meth:`DiscoveredPlugins.capabilities` to let callers probe
    what a plugin supports without importing protocol classes.
    """

    RUNTIME_CONSUMER = auto()
    """Plugin consumes a resolved runtime executable (e.g. pip, uv)."""

    PLUGIN_MANAGER = auto()
    """Plugin manages its own sub-plugins natively (e.g. pdm, poetry)."""

    MANIFEST_CONTRIBUTOR = auto()
    """Plugin contributes a manifest source file (e.g. pyproject.toml)."""

    RUNTIME_PROVIDER = auto()
    """Plugin provides managed runtime installations (e.g. pim, pyenv)."""


@dataclass(slots=True)
class PluginInfo:
    """Metadata about a discovered plugin.

    Args:
        name: Canonical plugin name (e.g. `"uv"`, `"pip"`).
        kind: The plugin kind (package, tool, project, runtime, scm).
        version: The version of the plugin distribution.
        installed: Whether the underlying tool is available on the system.
        tool_version: The PEP 440 version of the underlying CLI tool, or `None`
            if the tool is unavailable or its version could not be determined.
        host_tool: When this entry represents a sub-plugin managed by
            another tool, the canonical name of that host tool
            (e.g. ``"pdm"``).  ``None`` for top-level plugins.
    """

    name: str
    kind: PluginKind
    version: Version
    installed: bool
    tool_version: Version | None
    host_tool: str | None = None


@dataclass(slots=True)
class PluginOperationResult:
    """Result of a plugin operation (install/upgrade/uninstall).

    Args:
        plugin_name: The name of the plugin that was operated on.
        success: Whether the operation succeeded.
        message: Human-readable message describing the result.
    """

    plugin_name: str
    success: bool
    message: str


@dataclass(slots=True)
class RuntimePackageResult:
    """Packages installed under a single resolved runtime.

    Returned by
    :meth:`PackageCommands.list_by_runtime
    <porringer.backend.command.package.PackageCommands.list_by_runtime>`
    — one instance per successfully queried runtime tag.

    Attributes:
        provider: Canonical name of the runtime-provider plugin
            (e.g. ``"pim"``).
        tag: The version tag (e.g. ``"3.14"``).
        executable: Absolute path to the resolved interpreter.
        packages: Packages reported by the queried plugin for this
            runtime.
    """

    provider: str
    tag: str
    executable: Path
    packages: list[Package]


@dataclass(slots=True)
class ScopedPackage:
    """A package annotated with the scope it was discovered in.

    Returned by :meth:`PackageCommands.list_all_scopes` — wraps a
    :class:`Package` with the scope label and optional directory
    that it was found in.

    Attributes:
        package: The underlying package identity.
        scope_label: Human-readable scope label (``"global"`` or the
            directory name/path).
        scope_path: The directory that was queried, or ``None`` for
            the global / default environment.
        plugin_name: Canonical name of the plugin that reported
            this package.
    """

    package: Package
    scope_label: str
    scope_path: Path | None
    plugin_name: str
