"""Plugin integration for plugin."""

"""Plugin implementation for npm project environment."""

from typing import override

from porringer.core.plugin_schema.project_environment import NodeProjectEnvironment


class NPMProjectEnvironment(NodeProjectEnvironment):
    """Project environment managed by npm.

    Delegates dependency resolution and lock-file synchronisation to
    `npm install` inside the project directory.
    """

    _project_evidence_files = ('package-lock.json', 'npm-shrinkwrap.json')
    _package_manager_names = ('npm@',)

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Npm project wraps the `npm` CLI."""
        return 'npm'
