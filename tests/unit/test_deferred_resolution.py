"""Tests for deferred action resolution — fail-fast and escalated logging.

Validates the distinction between *deferrable* actions (plugin registered
but temporarily unavailable) and *permanently unresolvable* actions (no
plugin registered at all for the ``(kind, ecosystem)`` pair).

See: https://github.com/synodic/porringer/issues/73
"""

import logging
from pathlib import Path
from typing import override

import pytest
from packaging.version import Version

from porringer.backend.command.core.action_builder import build_actions
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import resolve_deferred_actions
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    PluginKind,
    PluginParameters,
)
from porringer.schema import SetupAction, SetupManifest

# ---------------------------------------------------------------------------
# Helpers — lightweight stub plugins (mirrors test_backend_resolver.py)
# ---------------------------------------------------------------------------

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


class _StubPlugin(Environment):
    """Minimal Environment subclass for testing."""

    @staticmethod
    def ecosystem() -> Ecosystem | None:
        return Ecosystem('python')

    @staticmethod
    def plugin_kind() -> PluginKind:
        return PluginKind.PACKAGE

    @staticmethod
    def is_supported() -> bool:
        return True

    @classmethod
    def is_available(cls) -> bool:
        return True

    @override
    def install_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['stub', 'install', package.name]

    @override
    def uninstall_command(self, package: PackageRef) -> list[str]:
        return ['stub', 'uninstall', package.name]

    @override
    def upgrade_command(self, package: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        return ['stub', 'upgrade', package.name]

    @override
    async def packages(self, *, project_path: Path | None = None) -> list[Package]:
        return []

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []


_DEFAULT_ECOSYSTEM = Ecosystem('python')


def _make(
    *,
    ecosystem: Ecosystem = _DEFAULT_ECOSYSTEM,
    kind: PluginKind = PluginKind.PACKAGE,
    supported: bool = True,
    available: bool = True,
) -> _StubPlugin:
    """Create a stub plugin with configurable behaviour."""

    class _Dynamic(_StubPlugin):
        @staticmethod
        def ecosystem() -> Ecosystem | None:
            return ecosystem

        @staticmethod
        def plugin_kind() -> PluginKind:
            return kind

        @staticmethod
        def is_supported() -> bool:
            return supported

        @classmethod
        def is_available(cls) -> bool:
            return available

    return _Dynamic(_PARAMS)


# ---------------------------------------------------------------------------
# Tests — build_actions fail-fast
# ---------------------------------------------------------------------------


class TestBuildActionsFailFast:
    """build_actions() distinguishes unregistered from registered-but-unavailable pairs."""

    @staticmethod
    def test_unregistered_ecosystem_produces_no_plugin_actions() -> None:
        """Requesting an ecosystem with no registered plugin produces actions with '(no plugin)' suffix."""
        manifest = SetupManifest.model_validate(
            {
                'packages': {'ruby': ['rails']},
            }
        )
        # Only a Python plugin is registered — 'ruby' has no plugin at all.
        environments: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'))}
        actions = build_actions(manifest, environments)
        assert len(actions) == 1
        assert actions[0].installer is None
        assert 'no plugin' in actions[0].description.lower()

    @staticmethod
    def test_registered_but_unavailable_defers() -> None:
        """Requesting an ecosystem whose plugin is registered but unavailable produces deferred actions."""
        manifest = SetupManifest.model_validate(
            {
                'packages': {'python': ['requests']},
            }
        )
        # Plugin is registered for python but not available (tool missing).
        environments: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'), available=False)}
        actions = build_actions(manifest, environments)
        assert len(actions) == 1
        assert actions[0].installer is None
        assert actions[0].kind == PluginKind.PACKAGE
        assert 'deferred' in actions[0].description.lower()

    @staticmethod
    def test_registered_and_available_resolves() -> None:
        """Requesting an ecosystem with an available plugin produces resolved actions."""
        manifest = SetupManifest.model_validate(
            {
                'packages': {'python': ['requests']},
            }
        )
        environments: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'))}
        actions = build_actions(manifest, environments)
        assert len(actions) == 1
        assert actions[0].installer == 'pip'


def test_unregistered_ecosystem_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    """Requesting an ecosystem with no registered plugin logs an ERROR."""
    manifest = SetupManifest.model_validate(
        {
            'packages': {'ruby': ['rails']},
        }
    )
    environments: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'))}
    with caplog.at_level(logging.DEBUG):
        build_actions(manifest, environments)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any('No plugin registered' in r.message for r in error_records)


# ---------------------------------------------------------------------------
# Tests — resolve_deferred_actions escalated logging
# ---------------------------------------------------------------------------


def test_still_unresolved_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    """Unresolved deferred actions produce an ERROR-level log."""
    # Create a deferred action for a registered-but-unavailable plugin.
    action = SetupAction(
        description="Install 'requests' (deferred)",
        kind=PluginKind.PACKAGE,
        ecosystem=Ecosystem('python'),
        installer=None,
        package=PackageRef(name='requests'),
    )
    # All plugins still unavailable at resolution time.
    envs: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'), available=False)}
    plugins = DiscoveredPlugins(
        environments=envs,
        project_environments={},
        scm_environments={},
    )

    with caplog.at_level(logging.DEBUG):
        resolve_deferred_actions([action], plugins)

    # Should have an ERROR-level message, not a WARNING.
    error_messages = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any('permanently unresolved' in r.message for r in error_messages), (
        f'Expected ERROR with "permanently unresolved", got: {[r.message for r in caplog.records]}'
    )


def test_resolved_action_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    """Successfully resolved deferred actions produce an INFO-level log."""
    action = SetupAction(
        description="Install 'requests' (deferred)",
        kind=PluginKind.PACKAGE,
        ecosystem=Ecosystem('python'),
        installer=None,
        package=PackageRef(name='requests'),
    )
    # Plugin is now available.
    envs: dict[str, Environment] = {'pip': _make(ecosystem=Ecosystem('python'), available=True)}
    plugins = DiscoveredPlugins(
        environments=envs,
        project_environments={},
        scm_environments={},
    )

    with caplog.at_level(logging.DEBUG):
        resolve_deferred_actions([action], plugins)

    assert action.installer == 'pip'
    info_messages = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any('resolved' in r.message.lower() for r in info_messages)


def test_preferences_threaded_through(caplog: pytest.LogCaptureFixture) -> None:
    """Preferences from the manifest are respected during deferred resolution."""
    action = SetupAction(
        description="Install 'requests' (deferred)",
        kind=PluginKind.PACKAGE,
        ecosystem=Ecosystem('python'),
        installer=None,
        package=PackageRef(name='requests'),
    )
    # Two available plugins — without preferences, 'alpha' wins alphabetically.
    envs: dict[str, Environment] = {
        'alpha': _make(ecosystem=Ecosystem('python'), available=True),
        'bravo': _make(ecosystem=Ecosystem('python'), available=True),
    }
    plugins = DiscoveredPlugins(
        environments=envs,
        project_environments={},
        scm_environments={},
    )
    preferences = {Ecosystem('python'): 'bravo'}

    resolve_deferred_actions([action], plugins, preferences=preferences)

    assert action.installer == 'bravo'
