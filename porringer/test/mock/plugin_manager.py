"""Mock plugin manager for testing routing logic without real tool dependencies.

Provides a ``MockPluginManager`` that records which operations were
requested (add vs update) so tests can assert on semantic intent
rather than exact CLI command strings.
"""

from typing import override

from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.schema import Package, PackageRef, PluginParameters

from .project_environment import MockProjectEnvironment


class MockPluginManager(MockProjectEnvironment, PluginManager):
    """In-memory plugin manager that records operations for assertion.

    All methods are side-effect-free — no subprocesses are started and
    no environment state is modified.

    Attributes:
        operations: Chronological list of ``(verb, PackageRef)`` tuples
            recorded by ``async_plugin_add`` and ``async_plugin_update``.
    """

    def __init__(
        self,
        params: PluginParameters,
        *,
        installed: list[Package] | None = None,
    ) -> None:
        """Initialize with optional pre-configured installed plugins.

        Args:
            params: Plugin parameters.
            installed: Plugins to report as already installed.
        """
        super().__init__(params)
        self._plugins = installed or []
        self.operations: list[tuple[str, PackageRef]] = []

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Fixed tool name for matching ``plugin_target`` in tests."""
        return 'mock-pm'

    @classmethod
    @override
    def is_available(cls) -> bool:
        """Always available — no real executable required."""
        return True

    # -- command builders (never executed, only used by get_cli_command) ------

    @override
    def plugin_add_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pm', 'add', plugin.specifier]

    @override
    def plugin_update_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pm', 'update', plugin.specifier]

    @override
    def plugin_remove_command(self, plugin: PackageRef) -> list[str]:
        return ['mock-pm', 'remove', plugin.name]

    @override
    def plugin_list_command(self) -> list[str]:
        return ['mock-pm', 'list']

    # -- query / execution (fully in-memory) ---------------------------------

    @override
    async def installed_plugins(self) -> list[Package]:
        return list(self._plugins)

    @override
    async def async_plugin_add(self, params: PackageParameters) -> Package | None:
        self.operations.append(('add', params.package))
        return Package(name=params.package.name, version=None)

    @override
    async def async_plugin_update(self, params: PackageParameters) -> Package | None:
        self.operations.append(('update', params.package))
        return Package(name=params.package.name, version=None)

    @override
    async def async_plugin_remove(self, params: PackageParameters) -> Package | None:
        self.operations.append(('remove', params.package))
        return Package(name=params.package.name, version=None)
