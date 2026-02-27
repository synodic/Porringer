"""Plugin implementation for Yarn (Berry v4+) project environment."""

import logging
from typing import override

from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)
from porringer.core.schema import Ecosystem

logger = logging.getLogger(__name__)


class YarnProjectEnvironment(ProjectEnvironment):
    """Project environment managed by Yarn Berry (v4+).

    Delegates dependency resolution and lock-file synchronisation to
    `yarn install` inside the project directory.

    Yarn Berry removed `yarn global`, so this plugin is
    **ProjectEnvironment-only** — there is no corresponding
    `YarnEnvironment` for global package installs.

    Overrides `sync()` because Yarn Berry does not support
    `--dry-run`.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Yarn project belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Yarn project consumes a Node runtime."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Yarn project wraps the `yarn` CLI."""
        return 'yarn'

    @override
    async def sync(self, params: ProjectSyncParameters) -> bool:
        """Run `yarn install` in the project directory.

        Yarn Berry does not support `--dry-run`.  In dry-run mode the
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
        return await self._run_sync(args, params.directory)
