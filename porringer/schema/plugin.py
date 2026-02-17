"""Plugin metadata schemas."""

from __future__ import annotations

from dataclasses import dataclass

from packaging.version import Version

from porringer.core.schema import PluginKind


@dataclass
class PluginInfo:
    """Metadata about a discovered plugin.

    Args:
        name: Canonical plugin name (e.g. `"uv"`, `"pip"`).
        kind: The plugin kind (package, tool, project, runtime, scm).
        version: The version of the plugin distribution.
        installed: Whether the underlying tool is available on the system.
        tool_version: The PEP 440 version of the underlying CLI tool, or `None`
            if the tool is unavailable or its version could not be determined.
    """

    name: str
    kind: PluginKind
    version: Version
    installed: bool
    tool_version: Version | None


@dataclass
class PluginOperationResult:
    """Result of a plugin operation (install/uninstall/update).

    Args:
        plugin_name: The name of the plugin that was operated on.
        success: Whether the operation succeeded.
        message: Human-readable message describing the result.
    """

    plugin_name: str
    success: bool
    message: str
