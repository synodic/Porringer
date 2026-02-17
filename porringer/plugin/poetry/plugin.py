"""Plugin implementation for Poetry project environment."""

from typing import override

from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)
from porringer.core.schema import Ecosystem, PackageRef


class PoetryProjectEnvironment(ProjectEnvironment, PluginManager):
    """Project environment managed by Poetry.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `poetry install`.

    Overrides `sync()` because Poetry requires a separate
    `poetry env use <path>` step to select a non-default interpreter,
    unlike PDM/uv which accept `--python` inline.

    Implements ``PluginManager`` so that declared sub-plugins are
    installed via ``poetry self add``.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Poetry belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Poetry consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Poetry wraps the `poetry` CLI."""
        return 'poetry'

    @override
    def plugin_add_command(self, plugin: PackageRef) -> list[str]:
        """Return ``poetry self add <plugin>``."""
        return ['poetry', 'self', 'add', plugin.specifier]

    @override
    def sync_command(self) -> list[str]:
        """Return the bare `poetry install` command.

        Poetry does not accept `--python` inline; runtime selection
        is handled by a separate `poetry env use` step in `sync()`.
        """
        return [self.tool_name(), self._sync_verb]

    @override
    def sync(self, params: ProjectSyncParameters) -> bool:
        """Runs `poetry install` in the project directory.

        If a runtime provider has resolved a Python interpreter, calls
        `poetry env use <path>` first so that Poetry targets the
        correct runtime.

        Args:
            params: Sync parameters.

        Returns:
            True on success.
        """
        # Poetry requires `env use` to select a non-default interpreter
        if self.runtime_executable is not None:
            env_args = ['poetry', 'env', 'use', str(self.runtime_executable)]
            if not self._run_sync(env_args, params.directory):
                return False

        args = list(self.sync_command())
        if params.dry:
            args.append('--dry-run')
        return self._run_sync(args, params.directory)
