"""Plugin implementation for Bun project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class BunProjectEnvironment(ProjectEnvironment):
    """Project environment managed by Bun.

    Delegates dependency resolution and lock-file synchronisation to
    `bun install` inside the project directory.  Bun is npm-compatible:
    it reads `package.json`, uses `node_modules`, and installs from
    the npm registry.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def ecosystem() -> str:
        """Bun project belongs to the `node` ecosystem."""
        return 'node'

    @staticmethod
    @override
    def default_priority() -> int:
        """Bun project is the lowest-priority Node project manager (priority 40)."""
        return 40

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Bun project consumes a Node runtime."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Bun project wraps the `bun` CLI."""
        return 'bun'
