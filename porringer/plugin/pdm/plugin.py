"""Plugin implementation for PDM project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class PdmProjectEnvironment(ProjectEnvironment):
    """Project environment managed by PDM.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `pdm install`.
    """

    @staticmethod
    @override
    def ecosystem() -> str:
        """PDM belongs to the `python` ecosystem."""
        return 'python'

    @staticmethod
    @override
    def default_priority() -> int:
        """PDM is a secondary Python project manager (priority 20)."""
        return 20

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """PDM consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """PDM wraps the `pdm` CLI."""
        return 'pdm'
