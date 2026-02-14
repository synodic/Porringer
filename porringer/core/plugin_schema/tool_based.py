"""Shared base for plugins backed by a command-line tool.

Provides the `tool_name()` / `is_available()` / `tool_version()` triple
so that `Environment`, `ProjectEnvironment`, and `ScmEnvironment` share
a single implementation instead of duplicating the `shutil.which` logic.
"""

import re
import shutil
import subprocess

from packaging.version import InvalidVersion, Version

from porringer.core.schema import Plugin, PluginParameters


class ToolBasedPlugin(Plugin):
    """Intermediate base for plugins backed by a CLI tool.

    Subclasses that override `tool_name()` to return a non-`None`
    string get automatic availability detection via `shutil.which`.
    Subclasses with `tool_name() → None` (the default) are always
    considered available.
    """

    def __init__(self, parameters: PluginParameters) -> None:
        """Initializes the tool-based plugin.

        Args:
            parameters: Plugin parameters including distribution info.
        """
        super().__init__(parameters)

    @classmethod
    def tool_name(cls) -> str | None:
        """Return the CLI executable name this plugin wraps.

        Override to declare which command-line tool the plugin uses.
        The base `is_available()` implementation uses this value
        with `shutil.which` to test whether the tool is on PATH.

        Returns `None` for plugins that are not backed by a single CLI
        tool (the default).  Those plugins are always considered available.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if the underlying tool is available on the system.

        When `tool_name()` returns a string, `shutil.which` is
        used to verify the executable exists on PATH.  When
        `tool_name()` returns `None`, the plugin is always
        considered available.
        """
        name = cls.tool_name()
        if name is None:
            return True
        return shutil.which(name) is not None

    @classmethod
    def tool_version(cls) -> Version | None:
        """Returns the PEP 440 version of the underlying CLI tool.

        The default implementation runs `<tool_name> --version`, extracts the
        first version-like pattern from the combined stdout/stderr output, and
        parses it as a `Version`.

        Returns `None` when `tool_name()` is `None`, the subprocess
        fails, or the output cannot be parsed as a valid PEP 440 version.

        Subclasses may override this method if their tool's version output
        requires special parsing.

        Returns:
            The parsed tool version, or `None`.
        """
        name = cls.tool_name()
        if name is None:
            return None

        try:
            result = subprocess.run(
                [name, '--version'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = result.stdout + result.stderr
        except OSError, subprocess.SubprocessError:
            return None

        match = re.search(r'v?\d+\.\d+(?:\.\d+)*', output)
        if match is None:
            return None

        try:
            return Version(match.group(0))
        except InvalidVersion:
            return None
