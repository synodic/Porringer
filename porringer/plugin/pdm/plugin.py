"""Plugin implementation for PDM project environment."""

from typing import override

from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem, Package, PackageRef


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

    @override
    def plugin_update_command(self, plugin: PackageRef) -> list[str]:
        """Return ``pdm self add --pip-args --upgrade <plugin>``.

        PDM's ``self update`` updates PDM itself and does not accept a
        package argument.  ``self add`` without extra flags is a no-op
        when the plugin is already installed, so ``--pip-args --upgrade``
        is required to force pip to pull a newer version.
        """
        return ['pdm', 'self', 'add', '--pip-args', '--upgrade', plugin.specifier]

    @override
    def plugin_list_command(self) -> list[str]:
        """Return ``pdm self list --plugins``."""
        return ['pdm', 'self', 'list', '--plugins']

    @staticmethod
    @override
    def parse_plugin_list(stdout: str) -> list[Package]:
        """Parse ``pdm self list --plugins`` output.

        Each line has the format ``name version [description...]``.
        """
        _min_versioned_parts = 2
        plugins: list[Package] = []
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= _min_versioned_parts:
                plugins.append(Package(name=parts[0], version=parts[1]))
            elif parts:
                plugins.append(Package(name=parts[0], version=None))
        return plugins
