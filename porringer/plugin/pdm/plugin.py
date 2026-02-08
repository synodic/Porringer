"""Plugin implementation for PDM project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class PdmProjectEnvironment(ProjectEnvironment):
    """Project environment managed by PDM.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to ``pdm install``.
    """

    @classmethod
    @override
    def tool_name(cls) -> str:
        """PDM wraps the ``pdm`` CLI."""
        return 'pdm'
