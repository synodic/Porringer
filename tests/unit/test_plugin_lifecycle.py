"""Helpers for test plugin lifecycle.

Contract tests for the Plugin setup/teardown lifecycle.

Verifies the framework-enforced guarantees:
  G1 — setup() is called at most once per plugin per sync.
  G2 — setup() failure aborts that plugin's actions, does not raise.
"""

import asyncio
from pathlib import Path
from typing import override
from unittest.mock import AsyncMock, patch

from packaging.version import Version

from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import ExecutionState
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    Plugin,
    PluginKind,
    PluginParameters,
)
from porringer.schema import (
    ActionCompletedEvent,
    ActionStartedEvent,
    SetupAction,
    SetupParameters,
    SetupResults,
)

# ---------------------------------------------------------------------------
# Fake plugin
# ---------------------------------------------------------------------------

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.1')))


class _FakeEnvironment(Environment):
    """Minimal concrete Environment for testing lifecycle calls."""

    def __init__(self, params: PluginParameters = _PARAMS) -> None:
        super().__init__(params)
        self.setup_calls: int = 0
        self.teardown_calls: int = 0
        self._packages: list[Package] = []

    @override
    async def setup(self) -> None:
        self.setup_calls += 1

    @override
    async def teardown(self) -> None:
        self.teardown_calls += 1

    @staticmethod
    def ecosystem() -> Ecosystem:
        return Ecosystem('test')

    @classmethod
    def tool_name(cls) -> str:
        return 'fake'

    @override
    def install_command(self, package: PackageRef, **_: object) -> list[str]:
        return ['fake', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef, **_: object) -> list[str]:
        return ['fake', 'upgrade', package.name]

    @override
    def uninstall_command(self, package: PackageRef, **_: object) -> list[str]:
        return ['fake', 'uninstall', package.name]

    @override
    async def packages(self, **_: object) -> list[Package]:
        return list(self._packages)

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        del params
        return []


class _FailingSetupEnvironment(_FakeEnvironment):
    """Environment whose setup() always raises."""

    @override
    async def setup(self) -> None:
        self.setup_calls += 1
        msg = 'setup boom'
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action(installer: str, name: str = 'pkg') -> SetupAction:
    return SetupAction(
        description=f'{installer}/{name}',
        kind=PluginKind.PACKAGE,
        installer=installer,
        package=PackageRef(name=name),
    )


def _make_state(
    actions: list[SetupAction],
    environments: dict[str, Environment],
) -> ExecutionState:
    """Build an ExecutionState with the minimum plumbing for lifecycle tests."""
    plugins = DiscoveredPlugins(
        environments=environments,
        project_environments={},
        scm_environments={},
    )
    preview = SetupResults(
        actions=actions,
        results=[],
        root_directory=Path('.'),
    )
    return ExecutionState(
        actions=actions,
        plugins=plugins,
        parameters=SetupParameters(),
        event_queue=asyncio.Queue(),
        manifest_directory=Path('.'),
        preview=preview,
    )


# ---------------------------------------------------------------------------
# G1 — idempotency gating
# ---------------------------------------------------------------------------


class TestSetupIdempotency:
    """setup() is called at most once per plugin per sync."""

    @staticmethod
    async def test_setup_called_once_for_multiple_actions() -> None:
        """Two actions for the same plugin produce exactly one setup() call."""
        env = _FakeEnvironment()
        actions = [_action('fake', 'a'), _action('fake', 'b')]
        state = _make_state(actions, {'fake': env})

        failed = await state._ensure_plugins_setup(actions)

        assert not failed
        assert env.setup_calls == 1

    @staticmethod
    async def test_setup_not_repeated_across_phases() -> None:
        """A second call to _ensure_plugins_setup for the same plugin is a no-op."""
        env = _FakeEnvironment()
        actions = [_action('fake')]
        state = _make_state(actions, {'fake': env})

        await state._ensure_plugins_setup(actions)
        await state._ensure_plugins_setup(actions)

        assert env.setup_calls == 1

    @staticmethod
    async def test_setup_per_plugin() -> None:
        """Each distinct plugin gets its own setup() call."""
        env_a = _FakeEnvironment()
        env_b = _FakeEnvironment()
        actions = [_action('a'), _action('b')]
        state = _make_state(actions, {'a': env_a, 'b': env_b})

        await state._ensure_plugins_setup(actions)

        assert env_a.setup_calls == 1
        assert env_b.setup_calls == 1

    @staticmethod
    async def test_no_setup_for_missing_environment() -> None:
        """Actions referencing an unknown installer silently skip setup."""
        actions = [_action('missing')]
        state = _make_state(actions, {})

        failed = await state._ensure_plugins_setup(actions)

        assert not failed


# ---------------------------------------------------------------------------
# G2 — soft-fail isolation
# ---------------------------------------------------------------------------


class TestSetupSoftFail:
    """setup() failure aborts the failing plugin; others continue."""

    @staticmethod
    async def test_failing_setup_returns_installer_name() -> None:
        """_ensure_plugins_setup returns the name of the failing plugin."""
        env = _FailingSetupEnvironment()
        actions = [_action('bad')]
        state = _make_state(actions, {'bad': env})

        failed = await state._ensure_plugins_setup(actions)

        assert 'bad' in failed

    @staticmethod
    async def test_failing_setup_does_not_block_others() -> None:
        """A good plugin's setup still runs even when another plugin fails."""
        bad = _FailingSetupEnvironment()
        good = _FakeEnvironment()
        actions = [_action('bad'), _action('good')]
        state = _make_state(actions, {'bad': bad, 'good': good})

        failed = await state._ensure_plugins_setup(actions)

        assert 'bad' in failed
        assert 'good' not in failed
        assert good.setup_calls == 1

    @staticmethod
    async def test_run_package_actions_skips_failed_plugin() -> None:
        """run_package_actions produces skip results for the failing plugin."""
        bad = _FailingSetupEnvironment()
        actions = [_action('bad', 'x')]
        state = _make_state(actions, {'bad': bad})

        # Mock execute_package_actions so we don't need real resolution
        with patch(
            'porringer.backend.command.core.execution.execute_package_actions',
            new=AsyncMock(return_value=([], True)),
        ) as mock_exec:
            results, ok = await state.run_package_actions(actions)

        # The action was removed from the dispatch list
        mock_exec.assert_awaited_once()
        dispatched_actions = mock_exec.call_args.args[0]
        assert len(dispatched_actions) == 0

        # A skip result was generated for the failed plugin's action
        assert len(results) == 1
        assert not results[0].success
        assert 'setup() failed' in (results[0].message or '')

        events = [state.event_queue.get_nowait(), state.event_queue.get_nowait()]
        assert isinstance(events[0], ActionStartedEvent)
        assert isinstance(events[1], ActionCompletedEvent)
        assert events[0].action_ref is not None
        assert events[0].action_ref.action_id == '0:0'
        assert events[1].action_ref == events[0].action_ref
        assert events[1].result is results[0]


# ---------------------------------------------------------------------------
# Default no-op inheritance
# ---------------------------------------------------------------------------


class TestDefaultNoOp:
    """Default setup/teardown are no-ops that every plugin inherits."""

    @staticmethod
    async def test_base_setup_is_noop() -> None:
        """Calling setup() on a plugin with no override is a silent no-op."""
        env = _FakeEnvironment.__new__(_FakeEnvironment)
        await Plugin.setup(env)

    @staticmethod
    async def test_base_teardown_is_noop() -> None:
        """Calling teardown() on a plugin with no override is a silent no-op."""
        env = _FakeEnvironment.__new__(_FakeEnvironment)

        await Plugin.teardown(env)
