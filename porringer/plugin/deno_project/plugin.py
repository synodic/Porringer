"""Plugin implementation for Deno project environment."""

import logging
from typing import override

from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)

logger = logging.getLogger(__name__)


class DenoProjectEnvironment(ProjectEnvironment):
    """Project environment managed by Deno.

    Delegates dependency resolution and lock-file synchronisation to
    ``deno install`` inside the project directory.  Deno reads
    dependencies from ``deno.json`` (or ``package.json`` for Node-compat
    projects).

    Overrides :meth:`sync` because Deno does not support ``--dry-run``
    on ``deno install``.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def ecosystem() -> str:
        """Deno project belongs to the ``deno`` ecosystem."""
        return 'deno'

    @staticmethod
    @override
    def default_priority() -> int:
        """Deno project is the sole Deno project manager (priority 10)."""
        return 10

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Deno project consumes a Deno runtime."""
        return 'deno'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Deno project wraps the ``deno`` CLI."""
        return 'deno'

    @override
    def sync(self, params: ProjectSyncParameters) -> bool:
        """Run ``deno install`` in the project directory.

        Deno does not support ``--dry-run``.  In dry-run mode the
        command is logged but not executed.

        Args:
            params: Sync parameters (directory, dry-run flag).

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        args = list(self.sync_command())
        if params.dry:
            logger.info('Dry run: %s', ' '.join(args))
            return True
        return self._run_sync(args, params.directory)
