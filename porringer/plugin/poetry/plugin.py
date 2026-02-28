"""Plugin implementation for Poetry project environment."""

import re
from typing import override

from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.project_environment import (
    ProjectEnvironment,
    ProjectSyncParameters,
)
from porringer.core.schema import Ecosystem, Package, PackageRef


class PoetryEnvironment(ProjectEnvironment, PluginManager):
    """Project environment managed by Poetry.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `poetry install`.

    Overrides `sync()` because Poetry requires a separate
    `poetry env use <path>` step to select a non-default interpreter,
    unlike PDM/uv which accept `--python` inline.

    Implements ``PluginManager`` so that declared sub-plugins are
    installed via ``poetry self add``.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Poetry belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Poetry consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Poetry wraps the `poetry` CLI."""
        return 'poetry'

    @override
    def plugin_add_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Return ``poetry self add <plugin>``.

        When *include_prereleases* is ``True``, appends
        ``--allow-prereleases`` so Poetry considers pre-release
        versions.
        """
        cmd = ['poetry', 'self', 'add', plugin.specifier]
        if include_prereleases:
            cmd.append('--allow-prereleases')
        return cmd

    @override
    def plugin_update_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Return ``poetry self add <plugin>``.

        Poetry's ``self add`` handles both initial install and
        upgrade, so this delegates to ``plugin_add_command``.
        """
        return self.plugin_add_command(plugin, include_prereleases=include_prereleases)

    @override
    def plugin_remove_command(self, plugin: PackageRef) -> list[str]:
        """Return ``poetry self remove <plugin>``."""
        return ['poetry', 'self', 'remove', plugin.name]

    @override
    def plugin_list_command(self) -> list[str]:
        """Return ``poetry self show plugins``."""
        return ['poetry', 'self', 'show', 'plugins']

    @staticmethod
    @override
    def parse_plugin_list(stdout: str) -> list[Package]:
        """Parse ``poetry self show plugins`` output.

        Poetry outputs plugin blocks starting with a line like
        ``  - <name> (<version>) <description>``.
        """
        plugins: list[Package] = []
        for line in stdout.splitlines():
            match = re.match(r'  - (\S+)\s+\((\S+)\)', line)
            if match:
                plugins.append(Package(name=match.group(1), version=match.group(2)))
        return plugins

    @override
    def sync_command(self) -> list[str]:
        """Return the bare `poetry install` command.

        Poetry does not accept `--python` inline; runtime selection
        is handled by a separate `poetry env use` step in `sync()`.
        """
        return [self.tool_name(), self._sync_verb]

    @override
    async def sync(self, params: ProjectSyncParameters) -> bool:
        """Runs `poetry install` in the project directory.

        If a runtime provider has resolved a Python interpreter, calls
        `poetry env use <path>` first so that Poetry targets the
        correct runtime.

        Args:
            params: Sync parameters.

        Returns:
            True on success.
        """
        # Poetry requires `env use` to select a non-default interpreter
        if self.runtime_executable is not None:
            env_args = ['poetry', 'env', 'use', str(self.runtime_executable)]
            if not await self._run_sync(env_args, params.directory):
                return False

        args = list(self.sync_command())
        if params.dry:
            args.append('--dry-run')
        return await self._run_sync(args, params.directory)
