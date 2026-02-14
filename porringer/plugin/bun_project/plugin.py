"""Plugin implementation for Bun project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem


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
    def ecosystem() -> Ecosystem:
        """Bun project belongs to the `node` ecosystem."""
        return Ecosystem('node')

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
