"""Plugin implementation for pnpm project environment."""

import logging
from typing import override

from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)
from porringer.core.schema import Ecosystem

logger = logging.getLogger(__name__)


class PNPMProjectEnvironment(ProjectEnvironment):
    """Project environment managed by pnpm.

    Delegates dependency resolution and lock-file synchronisation to
    `pnpm install` inside the project directory.

    Overrides `sync()` because pnpm does not support `--dry-run`.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Pnpm project belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Pnpm project consumes a Node runtime."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pnpm project wraps the `pnpm` CLI."""
        return 'pnpm'

    @override
    def sync(self, params: ProjectSyncParameters) -> bool:
        """Run `pnpm install` in the project directory.

        pnpm does not support `--dry-run`.  In dry-run mode the
        command is logged but not executed.

        Args:
            params: Sync parameters (directory, dry-run flag).

        Returns:
            `True` on success, `False` on failure.
        """
        args = list(self.sync_command())
        if params.dry:
            logger.info('Dry run: %s', ' '.join(args))
            return True
        return self._run_sync(args, params.directory)
