"""Shared base for plugins backed by a command-line tool.

Provides the :meth:`tool_name` / :meth:`is_available` pair so that
:class:`~porringer.core.plugin_schema.environment.Environment`,
:class:`~porringer.core.plugin_schema.project_environment.ProjectEnvironment`,
and :class:`~porringer.core.plugin_schema.scm.ScmEnvironment` share a
single implementation instead of duplicating the ``shutil.which`` logic.
"""

import shutil

from porringer.core.schema import Plugin, PluginParameters


class ToolBasedPlugin(Plugin):
    """Intermediate base for plugins backed by a CLI tool.

    Subclasses that override :meth:`tool_name` to return a non-``None``
    string get automatic availability detection via ``shutil.which``.
    Subclasses with ``tool_name() → None`` (the default) are always
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
        The base :meth:`is_available` implementation uses this value
        with ``shutil.which`` to test whether the tool is on PATH.

        Returns ``None`` for plugins that are not backed by a single CLI
        tool (the default).  Those plugins are always considered available.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if the underlying tool is available on the system.

        When :meth:`tool_name` returns a string, ``shutil.which`` is
        used to verify the executable exists on PATH.  When
        :meth:`tool_name` returns ``None``, the plugin is always
        considered available.
        """
        name = cls.tool_name()
        if name is None:
            return True
        return shutil.which(name) is not None
