"""Plugin implementation for npm project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem


class NpmProjectEnvironment(ProjectEnvironment):
    """Project environment managed by npm.

    Delegates dependency resolution and lock-file synchronisation to
    `npm install` inside the project directory.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Npm project belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Npm project consumes a Node runtime."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm project wraps the `npm` CLI."""
        return 'npm'
