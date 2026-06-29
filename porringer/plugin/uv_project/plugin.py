"""Plugin integration for plugin."""

"""Plugin implementation for UV project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem


class UvProjectEnvironment(ProjectEnvironment):
    """Project environment managed by uv.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `uv sync`.
    """

    _sync_verb: str = 'sync'
    _project_evidence_files = ('uv.lock',)
    _pyproject_tool_tables = (('tool', 'uv'),)

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """UV project belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """UV project consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """UV project wraps the `uv` CLI."""
        return 'uv'
