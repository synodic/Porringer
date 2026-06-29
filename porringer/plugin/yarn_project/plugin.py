"""Plugin integration for plugin.

Plugin implementation for Yarn (Berry v4+) project environment.
"""

from typing import override

from porringer.core.plugin_schema.project_environment import NodeProjectEnvironment


class YarnProjectEnvironment(NodeProjectEnvironment):
    """Project environment managed by Yarn Berry (v4+).

    Delegates dependency resolution and lock-file synchronisation to
    `yarn install` inside the project directory.

    Yarn Berry removed `yarn global`, so this plugin is
    **ProjectEnvironment-only** — there is no corresponding
    `YarnEnvironment` for global package installs.

    Yarn Berry does not support `--dry-run`, so dry runs log the
    command without executing it.
    """

    _supports_dry_run: bool = False
    _project_evidence_files = ('yarn.lock',)
    _package_manager_names = ('yarn@',)

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Yarn project wraps the `yarn` CLI."""
        return 'yarn'
