"""Plugin integration for plugin."""

"""Plugin implementation for pnpm project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import NodeProjectEnvironment


class PNPMProjectEnvironment(NodeProjectEnvironment):
    """Project environment managed by pnpm.

    Delegates dependency resolution and lock-file synchronisation to
    `pnpm install` inside the project directory.

    pnpm does not support `--dry-run`, so dry runs log the command
    without executing it.
    """

    _supports_dry_run: bool = False
    _project_evidence_files = ('pnpm-lock.yaml',)
    _package_manager_names = ('pnpm@',)

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pnpm project wraps the `pnpm` CLI."""
        return 'pnpm'
