"""Plugin implementation for PDM project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem


class PdmProjectEnvironment(ProjectEnvironment):
    """Project environment managed by PDM.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `pdm install`.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """PDM belongs to the `python` ecosystem."""
        return Ecosystem('python')

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
