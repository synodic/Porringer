"""Tests for runtime propagation across phase transitions and venv scoping."""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import override
from unittest.mock import patch

from packaging.version import Version

from porringer.backend.command.action_builder import PHASE_ORDER
from porringer.backend.command.execution import ExecutionState, execute_single
from porringer.core.plugin_schema.environment import Environment
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
    def install_command(self, package: PackageRef) -> list[str]:
        return ['mock-pim', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        return ['mock-pim', 'upgrade', str(package)]

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
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
    def install_command(self, package: PackageRef) -> list[str]:
        return ['mock-pip', 'install', str(package)]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
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
    def install_command(self, package: PackageRef) -> list[str]:
        return []

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        return []

    @override
    def packages(self, *, project_path: Path | None = None) -> list[Package]:
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


class TestRuntimePropagationAfterPhaseTransition:
    """Verify cached runtime is re-applied after plugin re-discovery."""

    @staticmethod
    def test_phase_transition_re_propagates_runtime() -> None:
        """Fresh consumers created by phase_transition receive the cached runtime."""
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

        # phase_transition replaces environments with new instances
        new_consumer = _MockPythonEnv(_MOCK_DIST)
        assert new_consumer.runtime_executable is None

        with patch('porringer.backend.command.execution.discover_plugins') as mock_discover:
            mock_discover.return_value = {'mock-pip': new_consumer}
            with patch('porringer.backend.command.execution.refresh_path'):
                state.phase_transition()

        assert state.environments['mock-pip'] is new_consumer
        assert new_consumer.runtime_executable == _MOCK_RUNTIME_EXE

    @staticmethod
    def test_phase_transition_noop_without_runtime() -> None:
        """Without a resolved runtime, phase_transition sets nothing."""
        state = _make_state(environments={'mock-pip': _MockPythonEnv(_MOCK_DIST)})
        assert state._resolved_runtime is None

        new_consumer = _MockPythonEnv(_MOCK_DIST)
        with patch('porringer.backend.command.execution.discover_plugins') as mock_discover:
            mock_discover.return_value = {'mock-pip': new_consumer}
            with patch('porringer.backend.command.execution.refresh_path'):
                state.phase_transition()

        assert new_consumer.runtime_executable is None

    @staticmethod
    def test_refresh_project_environments_re_propagates_runtime() -> None:
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

        with patch('porringer.backend.command.execution.discover_plugins') as mock_discover:
            mock_discover.return_value = {'mock-pdm': new_proj_env}
            state.refresh_project_environments()

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
        with patch('porringer.backend.command.execution.discover_plugins') as mock_discover:
            mock_discover.return_value = {'mock-npm': new_node_env}
            with patch('porringer.backend.command.execution.refresh_path'):
                state.phase_transition()

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
    def test_execute_single_phases_no_project_path() -> None:
        """Phase 2a and 2b call sites must not pass project_path."""
        source = inspect.getsource(execute_single)

        phase_2a_start = source.index('Phase 2a')
        phase_2b_start = source.index('Phase 2b')
        phase_3_start = source.index('Phase 3')

        assert 'project_path' not in source[phase_2a_start:phase_2b_start], 'Phase 2a should not pass project_path'
        assert 'project_path' not in source[phase_2b_start:phase_3_start], 'Phase 2b should not pass project_path'
