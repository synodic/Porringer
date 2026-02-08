"""Plugin implementation for npm project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class NpmProjectEnvironment(ProjectEnvironment):
    """Project environment managed by npm.

    Delegates dependency resolution and lock-file synchronisation to
    ``npm install`` inside the project directory.
    """

    _sync_verb: str = 'install'

    @staticmethod
    @override
    def package_backend() -> str:
        """Npm project manages the ``node-project`` backend."""
        return 'node-project'

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Npm project consumes a Node runtime."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm project wraps the ``npm`` CLI."""
        return 'npm'
