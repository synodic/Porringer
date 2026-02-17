"""Plugin implementation for PDM project environment."""

from typing import override

from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem, PackageRef


class PdmProjectEnvironment(ProjectEnvironment, PluginManager):
    """Project environment managed by PDM.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `pdm install`.

    Implements ``PluginManager`` so that declared sub-plugins are
    installed via ``pdm self add``.
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

    @override
    def plugin_add_command(self, plugin: PackageRef) -> list[str]:
        """Return ``pdm self add <plugin>``."""
        return ['pdm', 'self', 'add', plugin.specifier]
