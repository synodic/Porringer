"""Tests for update detection during dry-run.

Covers the new ``SkipReason.UPDATE_AVAILABLE`` path, version fields
on ``SetupActionResult``, and the ``detect_updates`` flag on
``SetupParameters``.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from porringer.backend.command.core.presence import (
    dry_run_action,
)
from porringer.backend.command.core.resolution import (
    UpdateCheckError,
    check_for_newer_version,
    is_package_installed,
)
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from porringer.schema.manifest import PackageSpec, PluginSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_action(
    name: str = 'ruff',
    constraint: str | None = None,
    installer: str = 'pip',
    kind: PluginKind = PluginKind.TOOL,
) -> SetupAction:
    pkg = PackageRef.model_validate(name if constraint is None else f'{name}{constraint}')
    return SetupAction(
        description=f"Install '{pkg}' via {installer}",
        kind=kind,
        ecosystem=Ecosystem('python'),
        installer=installer,
        package=pkg,
    )


def _make_env(
    installed: list[Package] | None = None,
    updates: list[Package] | None = None,
) -> MagicMock:
    """Create a mock ``Environment``."""
    env = MagicMock(spec=Environment)
    env.packages.return_value = installed or []
    env.check_updates.return_value = updates or []
    type(env).package_name_validator = MagicMock(return_value='pep440')
    env.tool_name.return_value = 'pip'
    return env


# ---------------------------------------------------------------------------
# is_package_installed — 3-tuple return
# ---------------------------------------------------------------------------


class TestIsPackageInstalledReturnsTuple3:
    """Verify the refactored 3-element return value."""

    @staticmethod
    def test_installed_returns_matched_package() -> None:
        """Installed package returns a match."""
        pkg = PackageRef.model_validate('ruff')
        installed = Package(name='ruff', version='0.8.0')
        ok, detail, matched = is_package_installed(pkg, [installed], 'pep440')
        assert ok is True
        assert matched is installed
        assert detail is not None

    @staticmethod
    def test_not_installed_returns_none() -> None:
        """Missing package returns None."""
        pkg = PackageRef.model_validate('ruff')
        ok, detail, matched = is_package_installed(pkg, [], 'pep440')
        assert ok is False
        assert matched is None
        assert detail is None


# ---------------------------------------------------------------------------
# check_for_newer_version
# ---------------------------------------------------------------------------


class TestCheckForNewerVersion:
    """Unit tests for the helper that queries a plugin for updates."""

    @staticmethod
    def test_returns_newer_version() -> None:
        """Newer version is returned when available."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        result = asyncio.run(check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0'))
        assert result == '0.9.0'

    @staticmethod
    def test_returns_none_when_up_to_date() -> None:
        """None is returned when already up to date."""
        env = _make_env(updates=[Package(name='ruff', version='0.8.0')])
        result = asyncio.run(check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0'))
        assert result is None

    @staticmethod
    def test_returns_none_when_plugin_has_no_updates() -> None:
        """None is returned when the plugin reports no updates."""
        env = _make_env(updates=[])
        result = asyncio.run(check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0'))
        assert result is None

    @staticmethod
    def test_raises_when_plugin_raises() -> None:
        """UpdateCheckError is raised when the plugin raises."""
        env = _make_env()
        env.check_updates.side_effect = RuntimeError('boom')
        with pytest.raises(UpdateCheckError):
            asyncio.run(check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0'))

    @staticmethod
    def test_forwards_include_prereleases() -> None:
        """The include_prereleases flag is forwarded to the plugin."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        asyncio.run(check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True))
        call_args = env.check_updates.call_args
        params: CheckUpdatesParameters = call_args[0][0]
        assert params.include_prereleases is True

    @staticmethod
    def test_returns_newer_prerelease() -> None:
        """Newer prerelease is returned when opted in."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        result = asyncio.run(
            check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True)
        )
        assert result == '0.9.0a1'

    @staticmethod
    def test_rejects_prerelease_when_not_opted_in() -> None:
        """When include_prereleases=False and the plugin leaks a prerelease, filter it out."""
        env = _make_env(updates=[Package(name='cppython', version='0.9.15.dev3')])
        result = asyncio.run(
            check_for_newer_version(env, PackageRef.model_validate('cppython'), '0.9.14', include_prereleases=False)
        )
        assert result is None

    @staticmethod
    def test_accepts_stable_when_not_opted_in() -> None:
        """When include_prereleases=False and the plugin returns a stable version, accept it."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        result = asyncio.run(
            check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=False)
        )
        assert result == '0.9.0'

    @staticmethod
    def test_rejects_devrelease_when_not_opted_in() -> None:
        """Dev releases like 1.0.0.dev1 are also filtered when include_prereleases=False."""
        env = _make_env(updates=[Package(name='foo', version='1.0.0.dev1')])
        result = asyncio.run(
            check_for_newer_version(env, PackageRef.model_validate('foo'), '0.9.0', include_prereleases=False)
        )
        assert result is None


# ---------------------------------------------------------------------------
# dry_run_action — UPDATE_AVAILABLE path
# ---------------------------------------------------------------------------


class TestDryRunUpdateAvailable:
    """The main integration of update detection in the dry-run flow."""

    @staticmethod
    def test_update_available_when_check_enabled() -> None:
        """When detect_updates=True and a newer version exists, result is UPDATE_AVAILABLE."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'
        assert result.message is not None

    @staticmethod
    def test_already_installed_when_no_update() -> None:
        """When detect_updates=True but no newer version, result is ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    def test_already_installed_when_check_disabled() -> None:
        """When detect_updates=False (default), check_updates is never called."""
        action = _make_action()
        env = _make_env(installed=[Package(name='ruff', version='0.8.0')])
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=False)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert result.installed_version == '0.8.0'
        env.check_updates.assert_not_called()

    @staticmethod
    def test_already_installed_when_no_parameters() -> None:
        """Legacy callers passing parameters=None get the old behavior."""
        action = _make_action()
        env = _make_env(installed=[Package(name='ruff', version='0.8.0')])
        envs = {'pip': env}

        result = dry_run_action(action, envs)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        env.check_updates.assert_not_called()

    @staticmethod
    def test_plugin_no_op_check_updates_falls_back() -> None:
        """Plugins that return [] from check_updates → ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # no-op plugin
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    def test_per_action_prereleases_forwarded() -> None:
        """Per-action include_prereleases is threaded to check_updates."""
        action = _make_action()
        action.include_prereleases = True
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True)

        dry_run_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True


# ---------------------------------------------------------------------------
# LATEST strategy — skip when already at latest
# ---------------------------------------------------------------------------


class TestLatestStrategySkip:
    """Verify that LATEST strategy skips packages already at the latest version."""

    @staticmethod
    def test_latest_skips_when_at_latest() -> None:
        """LATEST + installed + no newer version → SKIP with ALREADY_LATEST."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],  # same version → no update
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    def test_latest_upgrades_when_newer_available() -> None:
        """LATEST + installed + newer version exists → not skipped (UPGRADE)."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(action, envs, parameters=params)

        # Dry-run maps UPGRADE to a success (non-skipped) result
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    def test_latest_installs_when_not_present() -> None:
        """LATEST + not installed → INSTALL (not skipped)."""
        action = _make_action()
        env = _make_env(installed=[], updates=[])
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True

    @staticmethod
    def test_latest_falls_back_to_upgrade_on_check_error() -> None:
        """LATEST + installed + check_updates raises → UPGRADE (conservative)."""
        action = _make_action()
        env = _make_env(installed=[Package(name='ruff', version='0.8.0')])
        env.check_updates.side_effect = RuntimeError('network error')
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(action, envs, parameters=params)

        # Should not skip — falls back to upgrade attempt
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    def test_latest_skip_has_no_available_version() -> None:
        """When LATEST skips (already at latest), available_version should be None."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # empty → confirmed up-to-date
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.available_version is None

    @staticmethod
    def test_latest_plugin_target_skips_when_at_latest() -> None:
        """Plugin-target actions under LATEST also skip when at latest."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='0.9.14')])  # same version
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(strategy=SyncStrategy.LATEST)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST

    @staticmethod
    def test_latest_plugin_target_upgrades_when_newer() -> None:
        """Plugin-target actions under LATEST do upgrade when newer version exists."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='1.0.0')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(strategy=SyncStrategy.LATEST)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True


class TestExactStrategySkip:
    """Verify that EXACT strategy also skips packages already at latest."""

    @staticmethod
    def test_exact_skips_when_at_latest() -> None:
        """EXACT + installed + no newer version → SKIP with ALREADY_LATEST."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.EXACT)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST

    @staticmethod
    def test_exact_upgrades_when_newer() -> None:
        """EXACT + installed + newer version → UPGRADE (not skipped)."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.EXACT)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True


# ---------------------------------------------------------------------------
# dry_run_action — top-level dispatch
# ---------------------------------------------------------------------------


class TestDryRunActionDispatch:
    """Verify the top-level dry_run_action passes parameters through."""

    @staticmethod
    def test_parameters_threaded_to_package_action() -> None:
        """Parameters are threaded through to the package action."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True)

        result = dry_run_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    def test_project_action_ignores_parameters() -> None:
        """PROJECT actions always return success regardless of parameters."""
        action = SetupAction(description='Sync project', kind=PluginKind.PROJECT, ecosystem=Ecosystem('python'))
        params = SetupParameters(dry_run=True, detect_updates=True)
        result = dry_run_action(action, {}, parameters=params)
        assert result.success is True
        assert result.skipped is False


# ---------------------------------------------------------------------------
# SetupParameters defaults
# ---------------------------------------------------------------------------


class TestSetupParametersDefaults:
    """Verify the new fields have sensible defaults."""

    @staticmethod
    def test_defaults() -> None:
        """Default parameters disable update detection."""
        params = SetupParameters()
        assert params.detect_updates is False

    @staticmethod
    def test_explicit_values() -> None:
        """Explicit values override defaults."""
        params = SetupParameters(detect_updates=True)
        assert params.detect_updates is True


# ---------------------------------------------------------------------------
# SetupActionResult version fields
# ---------------------------------------------------------------------------


class TestSetupActionResultVersionFields:
    """Verify the new version fields on the result dataclass."""

    @staticmethod
    def test_defaults_are_none() -> None:
        """Version fields default to None."""
        action = _make_action()
        result = SetupActionResult(action=action, success=True)
        assert result.installed_version is None
        assert result.available_version is None

    @staticmethod
    def test_explicit_values() -> None:
        """Explicit version values are preserved."""
        action = _make_action()
        result = SetupActionResult(
            action=action,
            success=True,
            installed_version='1.0.0',
            available_version='2.0.0',
        )
        assert result.installed_version == '1.0.0'
        assert result.available_version == '2.0.0'


# ---------------------------------------------------------------------------
# Plugin-target update detection
# ---------------------------------------------------------------------------


def _make_plugin_action(
    name: str = 'cppython',
    installer: str = 'pipx',
    plugin_target: str = 'pdm',
    include_prereleases: bool = False,
) -> SetupAction:
    """Create a plugin-target SetupAction (e.g. cppython added to pdm)."""
    return SetupAction(
        description=f"Add plugin '{name}' to '{plugin_target}' via {installer}",
        kind=PluginKind.TOOL,
        ecosystem=Ecosystem('python'),
        installer=installer,
        package=PackageRef.model_validate(name),
        plugin_target=PackageRef.model_validate(plugin_target),
        include_prereleases=include_prereleases,
    )


class TestPluginTargetUpdateDetection:
    """Verify update detection for plugin-management actions (e.g. cppython→pdm)."""

    @staticmethod
    def test_plugin_update_available() -> None:
        """When detect_updates=True and a newer version exists, plugin gets UPDATE_AVAILABLE."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='1.0.0')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True, detect_updates=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.installed_version == '0.9.14'
        assert result.available_version == '1.0.0'

    @staticmethod
    def test_plugin_no_update() -> None:
        """When detect_updates=True but no newer version, plugin gets ALREADY_INSTALLED."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='0.9.14')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True, detect_updates=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    def test_plugin_detect_updates_off() -> None:
        """When detect_updates=False, plugin never calls check_updates."""
        action = _make_plugin_action()
        env = _make_env()
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True, detect_updates=False)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        env.check_updates.assert_not_called()

    @staticmethod
    def test_plugin_per_package_prereleases() -> None:
        """Per-package include_prereleases on a plugin action is threaded to check_updates."""
        action = _make_plugin_action(include_prereleases=True)
        env = _make_env(
            updates=[Package(name='cppython', version='1.0.0a1')],
        )
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins.return_value = [Package(name='cppython', version='0.9.14')]
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True, detect_updates=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        # Per-action flag is threaded through
        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE


# ---------------------------------------------------------------------------
# prerelease_packages override on SetupParameters
# ---------------------------------------------------------------------------


class TestPrereleasePackagesOverride:
    """Verify that SetupParameters.prerelease_packages mutates actions."""

    @staticmethod
    def test_override_sets_include_prereleases() -> None:
        """An action whose package is in prerelease_packages gets include_prereleases=True."""
        action = _make_action(name='ruff')
        assert not action.include_prereleases

        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Simulate what _load_manifests does: mutate the action
        params = SetupParameters(dry_run=True, detect_updates=True, prerelease_packages={'ruff'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action.include_prereleases = True

        assert action.include_prereleases

        result = dry_run_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    def test_override_case_insensitive() -> None:
        """Override matching is case-insensitive."""
        action = _make_action(name='Ruff')
        params = SetupParameters(prerelease_packages={'ruff'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action.include_prereleases = True
        assert action.include_prereleases is True

    @staticmethod
    def test_no_override_when_not_in_set() -> None:
        """Actions whose package is not in the set are left unchanged."""
        action = _make_action(name='ruff')
        params = SetupParameters(prerelease_packages={'black'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action.include_prereleases = True
        assert action.include_prereleases is False

    @staticmethod
    def test_no_override_when_none() -> None:
        """When prerelease_packages is None, nothing is mutated."""
        action = _make_action(name='ruff')
        params = SetupParameters(prerelease_packages=None)
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action.include_prereleases = True
        assert action.include_prereleases is False

    @staticmethod
    def test_default_is_none() -> None:
        """prerelease_packages defaults to None."""
        params = SetupParameters()
        assert params.prerelease_packages is None

    @staticmethod
    def test_override_threaded_to_check_for_newer_version() -> None:
        """Full pipeline: override → action mutation → check_updates receives True."""
        action = _make_action(name='ruff')
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Apply override (mirrors _load_manifests logic)
        action.include_prereleases = True

        params = SetupParameters(dry_run=True, detect_updates=True)
        result = dry_run_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.available_version == '0.9.0a1'


# ---------------------------------------------------------------------------
# PluginSpec — manifest-level per-plugin include_prereleases
# ---------------------------------------------------------------------------


class TestPluginSpec:
    """Verify that PluginSpec allows per-plugin include_prereleases in manifests."""

    @staticmethod
    def test_string_shorthand() -> None:
        """A plain string coerces to PluginSpec with defaults."""
        spec = PluginSpec.model_validate('cppython')
        assert spec.name.name == 'cppython'
        assert spec.include_prereleases is False
        assert spec.description is None

    @staticmethod
    def test_object_form_with_prereleases() -> None:
        """An object with include_prereleases=true is parsed correctly."""
        spec = PluginSpec.model_validate({'name': 'cppython', 'include_prereleases': True})
        assert spec.name.name == 'cppython'
        assert spec.include_prereleases is True

    @staticmethod
    def test_object_form_with_constraint() -> None:
        """An object with a versioned name is parsed correctly."""
        spec = PluginSpec.model_validate({'name': 'cppython>=1.0'})
        assert spec.name.name == 'cppython'
        assert spec.name.constraint == '>=1.0'
        assert spec.include_prereleases is False

    @staticmethod
    def test_package_spec_plugins_accepts_mixed() -> None:
        """PackageSpec.plugins accepts a mix of strings and objects."""
        spec = PackageSpec.model_validate({
            'name': 'pdm',
            'plugins': [
                'cppython',
                {'name': 'another-plugin', 'include_prereleases': True},
            ],
        })
        expected_plugin_count = 2
        assert len(spec.plugins) == expected_plugin_count
        assert spec.plugins[0].name.name == 'cppython'
        assert spec.plugins[0].include_prereleases is False
        assert spec.plugins[1].name.name == 'another-plugin'
        assert spec.plugins[1].include_prereleases is True

    @staticmethod
    def test_plugin_prereleases_independent_of_parent() -> None:
        """Plugin include_prereleases does not inherit from the parent PackageSpec."""
        spec = PackageSpec.model_validate({
            'name': 'pdm',
            'include_prereleases': True,
            'plugins': ['cppython'],
        })
        # Parent has include_prereleases=True, but the plugin string shorthand defaults to False
        assert spec.include_prereleases is True
        assert spec.plugins[0].include_prereleases is False

    @staticmethod
    def test_action_builder_uses_plugin_prereleases() -> None:
        """The action builder reads include_prereleases from the PluginSpec, not the parent."""
        # Build a plugin action with include_prereleases=True on the plugin
        action = _make_plugin_action(include_prereleases=True)
        assert action.include_prereleases is True

        # Build one without — defaults to False
        action2 = _make_plugin_action(include_prereleases=False)
        assert action2.include_prereleases is False
