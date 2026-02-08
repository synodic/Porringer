"""Plugin implementation for UV project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class UvProjectEnvironment(ProjectEnvironment):
    """Project environment managed by uv.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to ``uv sync``.
    """

    _sync_verb: str = 'sync'

    @staticmethod
    @override
    def package_backend() -> str:
        """UV project manages the ``python-project`` backend."""
        return 'python-project'

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """UV project consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV project wraps the ``uv`` CLI."""
        return 'uv'
