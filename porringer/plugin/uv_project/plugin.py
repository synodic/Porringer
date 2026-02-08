"""Plugin implementation for UV project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class UvProjectEnvironment(ProjectEnvironment):
    """Project environment managed by uv.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to ``uv sync``.
    """

    _sync_verb: str = 'sync'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV project wraps the ``uv`` CLI."""
        return 'uv'
