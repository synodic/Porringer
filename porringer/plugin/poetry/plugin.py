"""Plugin implementation for Poetry project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)


class PoetryProjectEnvironment(ProjectEnvironment):
    """Project environment managed by Poetry.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to ``poetry install``.

    Overrides :meth:`sync` because Poetry requires a separate
    ``poetry env use <path>`` step to select a non-default interpreter,
    unlike PDM/uv which accept ``--python`` inline.
    """

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Poetry wraps the ``poetry`` CLI."""
        return 'poetry'

    @override
    def sync_command(self) -> list[str]:
        """Return the bare ``poetry install`` command.

        Poetry does not accept ``--python`` inline; runtime selection
        is handled by a separate ``poetry env use`` step in :meth:`sync`.
        """
        return [self.tool_name(), self._sync_verb]

    @override
    def sync(self, params: ProjectSyncParameters) -> bool:
        """Runs ``poetry install`` in the project directory.

        If a runtime provider has resolved a Python interpreter, calls
        ``poetry env use <path>`` first so that Poetry targets the
        correct runtime.

        Args:
            params: Sync parameters.

        Returns:
            True on success.
        """
        # Poetry requires `env use` to select a non-default interpreter
        if self.python_executable is not None:
            env_args = ['poetry', 'env', 'use', str(self.python_executable)]
            if not self._run_sync(env_args, params.directory):
                return False

        args = list(self.sync_command())
        if params.dry:
            args.append('--dry-run')
        return self._run_sync(args, params.directory)
