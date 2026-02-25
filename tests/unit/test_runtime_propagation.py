"""Tests for runtime propagation across phase transitions and venv scoping."""

import inspect
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import override
from unittest.mock import patch

from packaging.version import Version

from porringer.backend.command.core.action_builder import PHASE_ORDER
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import ExecutionState
from porringer.backend.command.core.phase import PackagePhase, ToolPhase
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeProvider
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import SetupAction, SetupParameters

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_DIST = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
_MOCK_RUNTIME_EXE = Path('/mock/runtimes/python3.14/python')

_RUNTIME_ACTION = SetupAction(
    description='Install python 3.14',
    kind=PluginKind.RUNTIME,
    installer='mock-pim',
    package=PackageRef(name='3.14'),
)


@contextmanager
def _preserve_path() -> Iterator[None]:
    """Save and restore the PATH environment variable."""
    original = os.environ.get('PATH', '')
    try:
        yield
    finally:
        os.environ['PATH'] = original


class _MockRuntimeProvider(Environment):
    """An environment that provides a Python runtime."""

    _resolved: Path | None = _MOCK_RUNTIME_EXE

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        return Ecosystem('python')

    @classmethod
    @override
    def tool_name(cls) -> str:
        return 'mock-pim'

    @classmethod
    def provided_runtime_kind(cls) -> str:
        """Return the kind of runtime this provider supplies."""
        return 'python'

    def resolve_executable(self, tag: str) -> Path | None:
        """Resolve a tagged runtime to a mock path."""
        return self._resolved

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pim', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pim', 'upgrade', str(package)]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        return []

    @override
    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []


# Register as RuntimeProvider via structural subtyping check
assert isinstance(_MockRuntimeProvider(_MOCK_DIST), RuntimeProvider)


class _MockPythonEnv(PythonEnvironment):
    """A minimal RuntimeConsumer environment (like pip or uv)."""

    @classmethod
    @override
    def tool_name(cls) -> str | None:
        return 'mock-pip'

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pip', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['mock-pip', 'upgrade', str(package)]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        return []


class _MockProjectEnv(ProjectEnvironment):
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


class _MockNodeConsumer(Environment, RuntimeConsumer):
    """An environment that consumes a Node runtime, not Python."""

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        return Ecosystem('node')

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        """Return the kind of runtime this consumer requires."""
        return 'node'

    @classmethod
    @override
    def tool_name(cls) -> str | None:
        return None

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return []

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return []

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
        return []

    @override
    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []


def _make_state(
    *,
    environments: dict[str, Environment] | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    runtime_actions: list[SetupAction] | None = None,
) -> ExecutionState:
    """Build a minimal ExecutionState for testing."""
    actions = runtime_actions or []
    phases: dict[PluginKind | None, list[SetupAction]] = {k: [] for k in PHASE_ORDER}
    if runtime_actions:
        phases[PluginKind.RUNTIME] = runtime_actions

    return ExecutionState(
        actions=actions,
        phases=phases,
        environments=environments or {},
        project_environments=project_environments,
        scm_environments=None,
        parameters=SetupParameters(),
        event_queue=None,
        manifest_directory=Path('.'),
        fallback_dir=Path('.'),
        skip_project=False,
    )


# ---------------------------------------------------------------------------
# Bug 1: Runtime propagation lost after phase transition
# ---------------------------------------------------------------------------


class TestRuntimePropagationAfterPluginRefresh:
    """Verify cached runtime is re-applied after plugin re-discovery."""

    @staticmethod
    def test_refresh_all_plugins_re_propagates_runtime() -> None:
        """Fresh consumers created by refresh_all_plugins receive the cached runtime."""
        provider = _MockRuntimeProvider(_MOCK_DIST)
        consumer = _MockPythonEnv(_MOCK_DIST)
        state = _make_state(
            environments={'mock-pim': provider, 'mock-pip': consumer},
            runtime_actions=[_RUNTIME_ACTION],
        )

        with _preserve_path():
            state.propagate_runtime()

        assert consumer.runtime_executable == _MOCK_RUNTIME_EXE
        assert state._resolved_runtime == ('python', _MOCK_RUNTIME_EXE)

        # refresh_all_plugins replaces environments with new instances
        new_consumer = _MockPythonEnv(_MOCK_DIST)
        assert new_consumer.runtime_executable is None

        with (
            patch('porringer.backend.command.core.execution.discover_all_plugins') as mock_discover,
            patch('porringer.backend.command.core.execution.refresh_path'),
            patch('porringer.backend.command.core.execution.invalidate_plugin_cache'),
        ):
            mock_discover.return_value = DiscoveredPlugins(
                environments={'mock-pip': new_consumer},
                project_environments={},
                scm_environments={},
            )
            state.refresh_all_plugins()

        assert state.environments['mock-pip'] is new_consumer
        assert new_consumer.runtime_executable == _MOCK_RUNTIME_EXE

    @staticmethod
    def test_refresh_all_plugins_noop_without_runtime() -> None:
        """Without a resolved runtime, refresh_all_plugins sets nothing."""
        state = _make_state(environments={'mock-pip': _MockPythonEnv(_MOCK_DIST)})
        assert state._resolved_runtime is None

        new_consumer = _MockPythonEnv(_MOCK_DIST)
        with (
            patch('porringer.backend.command.core.execution.discover_all_plugins') as mock_discover,
            patch('porringer.backend.command.core.execution.refresh_path'),
            patch('porringer.backend.command.core.execution.invalidate_plugin_cache'),
        ):
            mock_discover.return_value = DiscoveredPlugins(
                environments={'mock-pip': new_consumer},
                project_environments={},
                scm_environments={},
            )
            state.refresh_all_plugins()

        assert new_consumer.runtime_executable is None

    @staticmethod
    def test_refresh_all_plugins_re_propagates_to_project_environments() -> None:
        """Fresh project environments from refresh receive the cached runtime."""
        provider = _MockRuntimeProvider(_MOCK_DIST)
        consumer = _MockPythonEnv(_MOCK_DIST)
        proj_env = _MockProjectEnv(_MOCK_DIST)
        state = _make_state(
            environments={'mock-pim': provider, 'mock-pip': consumer},
            project_environments={'mock-pdm': proj_env},
            runtime_actions=[_RUNTIME_ACTION],
        )

        with _preserve_path():
            state.propagate_runtime()
        assert proj_env.runtime_executable == _MOCK_RUNTIME_EXE

        new_proj_env = _MockProjectEnv(_MOCK_DIST)
        assert new_proj_env.runtime_executable is None

        with (
            patch('porringer.backend.command.core.execution.discover_all_plugins') as mock_discover,
            patch('porringer.backend.command.core.execution.refresh_path'),
            patch('porringer.backend.command.core.execution.invalidate_plugin_cache'),
        ):
            mock_discover.return_value = DiscoveredPlugins(
                environments={'mock-pim': provider, 'mock-pip': consumer},
                project_environments={'mock-pdm': new_proj_env},
                scm_environments={},
            )
            state.refresh_all_plugins()

        assert state.project_environments is not None
        assert state.project_environments['mock-pdm'] is new_proj_env
        assert new_proj_env.runtime_executable == _MOCK_RUNTIME_EXE

    @staticmethod
    def test_non_matching_runtime_kind_not_propagated() -> None:
        """A Node consumer does not receive the Python runtime."""
        provider = _MockRuntimeProvider(_MOCK_DIST)
        node_env = _MockNodeConsumer(_MOCK_DIST)
        state = _make_state(
            environments={'mock-pim': provider, 'mock-npm': node_env},
            runtime_actions=[_RUNTIME_ACTION],
        )

        with _preserve_path():
            state.propagate_runtime()
        assert node_env.runtime_executable is None

        new_node_env = _MockNodeConsumer(_MOCK_DIST)
        with (
            patch('porringer.backend.command.core.execution.discover_all_plugins') as mock_discover,
            patch('porringer.backend.command.core.execution.refresh_path'),
            patch('porringer.backend.command.core.execution.invalidate_plugin_cache'),
        ):
            mock_discover.return_value = DiscoveredPlugins(
                environments={'mock-npm': new_node_env},
                project_environments={},
                scm_environments={},
            )
            state.refresh_all_plugins()

        assert new_node_env.runtime_executable is None


# ---------------------------------------------------------------------------
# Bug 2: PACKAGE/TOOL phases should not pass project_path
# ---------------------------------------------------------------------------


class TestPackagePhaseNoProjectPath:
    """Verify Phase 2a/2b do not scope presence checks to a project venv."""

    @staticmethod
    def test_python_command_uses_global_without_project_path() -> None:
        """Without project_path, python_command returns the runtime executable."""
        env = _MockPythonEnv(_MOCK_DIST)
        env.runtime_executable = _MOCK_RUNTIME_EXE
        assert env.python_command == str(_MOCK_RUNTIME_EXE)

    @staticmethod
    def test_venv_discovered_only_with_project_path(tmp_path: Path) -> None:
        """_discover_venv_python returns a path only when a .venv exists."""
        if os.name == 'nt':
            venv_python = tmp_path / '.venv' / 'Scripts' / 'python.exe'
        else:
            venv_python = tmp_path / '.venv' / 'bin' / 'python'
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()

        assert PythonEnvironment._discover_venv_python(tmp_path) == venv_python
        assert PythonEnvironment._discover_venv_python(Path('/nonexistent')) is None

    @staticmethod
    def test_package_and_tool_phases_no_project_path() -> None:
        """PackagePhase and ToolPhase execute methods must not pass project_path."""
        package_source = inspect.getsource(PackagePhase.execute)
        tool_source = inspect.getsource(ToolPhase.execute)

        assert 'project_path' not in package_source, 'PackagePhase.execute should not pass project_path'
        assert 'project_path' not in tool_source, 'ToolPhase.execute should not pass project_path'
