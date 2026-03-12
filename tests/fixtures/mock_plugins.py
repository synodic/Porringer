"""Shared mock plugin classes for unit tests.

These mock implementations of ``Environment``, ``RuntimeProvider``,
``PythonEnvironment``, ``ProjectEnvironment``, and ``RuntimeConsumer``
are used across multiple test modules.  Centralising them avoids
duplication and ensures consistent test behaviour.
"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import override

from packaging.version import Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext, RuntimeProvider
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters

MOCK_DIST = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
MOCK_RUNTIME_EXE = Path('/mock/runtimes/python3.14/python')

RUNTIME_ACTION_TEMPLATE = {
    'description': 'Install python 3.14',
    'kind': PluginKind.RUNTIME,
    'installer': 'mock-pim',
    'package': PackageRef(name='3.14'),
}
"""Keyword arguments for constructing a ``SetupAction`` that installs
a mock Python runtime.  Import and expand via
``SetupAction(**RUNTIME_ACTION_TEMPLATE)``."""


@contextmanager
def preserve_path() -> Generator[None]:
    """Save and restore the PATH environment variable."""
    original = os.environ.get('PATH', '')
    try:
        yield
    finally:
        os.environ['PATH'] = original


class MockRuntimeProvider(Environment, RuntimeProvider):
    """An environment that provides a Python runtime."""

    _resolved: Path | None = MOCK_RUNTIME_EXE

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        return Ecosystem('python')

    @classmethod
    @override
    def tool_name(cls) -> str:
        return 'mock-pim'

    @classmethod
    @override
    def provided_runtime_kind(cls) -> str:
        return 'python'

    @override
    async def resolve_executable(self, tag: str) -> Path | None:
        return self._resolved

    @override
    async def available_tags(self) -> list[str]:
        return ['3.14'] if self._resolved is not None else []

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['mock-pim', 'install', str(package)]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        return ['mock-pim', 'uninstall', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['mock-pim', 'upgrade', str(package)]

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        return []

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []


class MockPythonEnv(PythonEnvironment):
    """A minimal RuntimeConsumer environment (like pip or uv)."""

    @classmethod
    @override
    def tool_name(cls) -> str | None:
        return 'mock-pip'

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['mock-pip', 'install', str(package)]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        return ['mock-pip', 'uninstall', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['mock-pip', 'upgrade', str(package)]

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        return []


class MockProjectEnv(ProjectEnvironment):
    """A minimal RuntimeConsumer project environment."""

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        return 'mock-pdm'


class MockNodeConsumer(Environment, RuntimeConsumer):
    """An environment that consumes a Node runtime, not Python."""

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        return Ecosystem('node')

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        """Identifier"""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str | None:
        return None

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return []

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        return []

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return []

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        return []

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []
