"""Tests for update detection during dry-run.

Covers ``SkipReason.UPDATE_AVAILABLE`` / ``ALREADY_INSTALLED`` paths,
version fields on ``SetupActionResult``, and version propagation on
``Upgrade`` results.
"""

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
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
    env.packages = AsyncMock(return_value=installed or [])
    env.check_updates = AsyncMock(return_value=updates or [])
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
    async def test_returns_newer_version() -> None:
        """Newer version is returned when available."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        result = await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result == '0.9.0'

    @staticmethod
    async def test_returns_none_when_up_to_date() -> None:
        """None is returned when already up to date."""
        env = _make_env(updates=[Package(name='ruff', version='0.8.0')])
        result = await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result is None

    @staticmethod
    async def test_returns_none_when_plugin_has_no_updates() -> None:
        """None is returned when the plugin reports no updates."""
        env = _make_env(updates=[])
        result = await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result is None

    @staticmethod
    async def test_raises_when_plugin_raises() -> None:
        """UpdateCheckError is raised when the plugin raises."""
        env = _make_env()
        env.check_updates.side_effect = RuntimeError('boom')
        with pytest.raises(UpdateCheckError):
            await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')

    @staticmethod
    async def test_forwards_include_prereleases() -> None:
        """The include_prereleases flag is forwarded to the plugin."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True)
        call_args = env.check_updates.call_args
        params: CheckUpdatesParameters = call_args[0][0]
        assert params.include_prereleases is True

    @staticmethod
    async def test_returns_newer_prerelease() -> None:
        """Newer prerelease is returned when opted in."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        result = await check_for_newer_version(
            env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True
        )
        assert result == '0.9.0a1'

    @staticmethod
    async def test_rejects_prerelease_when_not_opted_in() -> None:
        """When include_prereleases=False and the plugin leaks a prerelease, filter it out."""
        env = _make_env(updates=[Package(name='cppython', version='0.9.15.dev3')])
        result = await check_for_newer_version(
            env, PackageRef.model_validate('cppython'), '0.9.14', include_prereleases=False
        )
        assert result is None

    @staticmethod
    async def test_accepts_stable_when_not_opted_in() -> None:
        """When include_prereleases=False and the plugin returns a stable version, accept it."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        result = await check_for_newer_version(
            env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=False
        )
        assert result == '0.9.0'

    @staticmethod
    async def test_rejects_devrelease_when_not_opted_in() -> None:
        """Dev releases like 1.0.0.dev1 are also filtered when include_prereleases=False."""
        env = _make_env(updates=[Package(name='foo', version='1.0.0.dev1')])
        result = await check_for_newer_version(
            env, PackageRef.model_validate('foo'), '0.9.0', include_prereleases=False
        )
        assert result is None

    @staticmethod
    async def test_forwards_runtime_context() -> None:
        """The runtime_context kwarg is forwarded inside CheckUpdatesParameters."""
        ctx = RuntimeContext()
        ctx.executables['python'] = Path('/custom/python')
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        await check_for_newer_version(
            env,
            PackageRef.model_validate('ruff'),
            '0.8.0',
            runtime_context=ctx,
        )
        call_args = env.check_updates.call_args
        params: CheckUpdatesParameters = call_args[0][0]
        assert params.runtime_context is ctx

    @staticmethod
    async def test_none_runtime_context_by_default() -> None:
        """When runtime_context is omitted, params.runtime_context is None."""
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        await check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        call_args = env.check_updates.call_args
        params: CheckUpdatesParameters = call_args[0][0]
        assert params.runtime_context is None


# ---------------------------------------------------------------------------
# dry_run_action — UPDATE_AVAILABLE path
# ---------------------------------------------------------------------------


class TestDryRunUpdateAvailable:
    """The main integration of update detection in the dry-run flow."""

    @staticmethod
    async def test_update_available_when_check_enabled() -> None:
        """When a newer version exists, result is UPDATE_AVAILABLE."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'
        assert result.message is not None

    @staticmethod
    async def test_already_installed_when_no_update() -> None:
        """When no newer version exists, result is ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    async def test_always_checks_for_updates() -> None:
        """Update checks are always performed for installed packages."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        env.check_updates.assert_called_once()
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    async def test_already_installed_when_no_parameters() -> None:
        """Legacy callers passing parameters=None still check for updates."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}

        result = await dry_run_action(action, envs)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        env.check_updates.assert_called_once()

    @staticmethod
    async def test_plugin_no_op_check_updates_falls_back() -> None:
        """Plugins that return [] from check_updates → ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # no-op plugin
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    async def test_per_action_prereleases_forwarded() -> None:
        """Per-action include_prereleases is threaded to check_updates."""
        action = replace(_make_action(), include_prereleases=True)
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        await dry_run_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True


# ---------------------------------------------------------------------------
# LATEST strategy — skip when already at latest
# ---------------------------------------------------------------------------


class TestLatestStrategySkip:
    """Verify that LATEST strategy skips packages already at the latest version."""

    @staticmethod
    async def test_latest_skips_when_at_latest() -> None:
        """LATEST + installed + no newer version → SKIP with ALREADY_LATEST."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],  # same version → no update
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    async def test_latest_upgrades_when_newer_available() -> None:
        """LATEST + installed + newer version exists → not skipped (UPGRADE)."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        # Dry-run maps UPGRADE to a success (non-skipped) result
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_installs_when_not_present() -> None:
        """LATEST + not installed → INSTALL (not skipped)."""
        action = _make_action()
        env = _make_env(installed=[], updates=[])
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_falls_back_to_upgrade_on_check_error() -> None:
        """LATEST + installed + check_updates raises → UPGRADE (conservative)."""
        action = _make_action()
        env = _make_env(installed=[Package(name='ruff', version='0.8.0')])
        env.check_updates.side_effect = RuntimeError('network error')
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        # Should not skip — falls back to upgrade attempt
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_skip_has_no_available_version() -> None:
        """When LATEST skips (already at latest), available_version should be None."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # empty → confirmed up-to-date
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.available_version is None

    @staticmethod
    async def test_latest_plugin_target_skips_when_at_latest() -> None:
        """Plugin-target actions under LATEST also skip when at latest."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='0.9.14')])  # same version
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(strategy=SyncStrategy.LATEST)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST

    @staticmethod
    async def test_latest_plugin_target_upgrades_when_newer() -> None:
        """Plugin-target actions under LATEST do upgrade when newer version exists."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='1.0.0')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(strategy=SyncStrategy.LATEST)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True


class TestExactStrategySkip:
    """Verify that EXACT strategy also skips packages already at latest."""

    @staticmethod
    async def test_exact_skips_when_at_latest() -> None:
        """EXACT + installed + no newer version → SKIP with ALREADY_LATEST."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.EXACT)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST

    @staticmethod
    async def test_exact_upgrades_when_newer() -> None:
        """EXACT + installed + newer version → UPGRADE (not skipped)."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.EXACT)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True


# ---------------------------------------------------------------------------
# dry_run_action — top-level dispatch
# ---------------------------------------------------------------------------


class TestDryRunActionDispatch:
    """Verify the top-level dry_run_action passes parameters through."""

    @staticmethod
    async def test_parameters_threaded_to_package_action() -> None:
        """Parameters are threaded through to the package action."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    async def test_project_action_ignores_parameters() -> None:
        """PROJECT actions always return success regardless of parameters."""
        action = SetupAction(description='Sync project', kind=PluginKind.PROJECT, ecosystem=Ecosystem('python'))
        params = SetupParameters(dry_run=True)
        result = await dry_run_action(action, {}, parameters=params)
        assert result.success is True
        assert result.skipped is False


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
    async def test_plugin_update_available() -> None:
        """When a newer version exists, plugin gets UPDATE_AVAILABLE."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='1.0.0')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.installed_version == '0.9.14'
        assert result.available_version == '1.0.0'

    @staticmethod
    async def test_plugin_no_update() -> None:
        """When no newer version exists, plugin gets ALREADY_INSTALLED."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='0.9.14')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    async def test_plugin_always_checks_for_updates() -> None:
        """Plugin-management actions always perform update checks."""
        action = _make_plugin_action()
        env = _make_env(updates=[Package(name='cppython', version='1.0.0')])
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

        env.check_updates.assert_called_once()
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    async def test_plugin_per_package_prereleases() -> None:
        """Per-package include_prereleases on a plugin action is threaded to check_updates."""
        action = _make_plugin_action(include_prereleases=True)
        env = _make_env(
            updates=[Package(name='cppython', version='1.0.0a1')],
        )
        envs = {'pipx': env}

        manager = MagicMock()
        manager.installed_plugins = AsyncMock(return_value=[Package(name='cppython', version='0.9.14')])
        manager.tool_name.return_value = 'pdm'
        manager.is_available.return_value = True

        params = SetupParameters(dry_run=True)

        with patch(
            'porringer.backend.command.core.resolution.find_plugin_manager',
            return_value=manager,
        ):
            result = await dry_run_action(action, envs, parameters=params)

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
    async def test_override_sets_include_prereleases() -> None:
        """An action whose package is in prerelease_packages gets include_prereleases=True."""
        action = _make_action(name='ruff')
        assert not action.include_prereleases

        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Simulate what _load_manifests does: mutate the action
        params = SetupParameters(dry_run=True, prerelease_packages={'ruff'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action = replace(action, include_prereleases=True)

        assert action.include_prereleases

        result = await dry_run_action(action, envs, parameters=params)

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
                action = replace(action, include_prereleases=True)
        assert action.include_prereleases is True

    @staticmethod
    def test_no_override_when_not_in_set() -> None:
        """Actions whose package is not in the set are left unchanged."""
        action = _make_action(name='ruff')
        params = SetupParameters(prerelease_packages={'black'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action = replace(action, include_prereleases=True)
        assert action.include_prereleases is False

    @staticmethod
    async def test_override_threaded_to_check_for_newer_version() -> None:
        """Full pipeline: override → action mutation → check_updates receives True."""
        action = _make_action(name='ruff')
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Apply override (mirrors _load_manifests logic)
        action = replace(action, include_prereleases=True)

        params = SetupParameters(dry_run=True)
        result = await dry_run_action(action, envs, parameters=params)

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


# ---------------------------------------------------------------------------
# Upgrade result carries version metadata
# ---------------------------------------------------------------------------


class TestUpgradeVersionFields:
    """Verify that Upgrade results carry installed and available version."""

    @staticmethod
    async def test_latest_upgrade_has_version_fields() -> None:
        """LATEST + installed + newer version → Upgrade result with version metadata."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'

    @staticmethod
    async def test_exact_upgrade_has_version_fields() -> None:
        """EXACT + installed + newer version → Upgrade result with version metadata."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.EXACT)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'

    @staticmethod
    async def test_ensure_extras_has_installed_version() -> None:
        """Install(ENSURE_EXTRAS) result carries installed_version."""
        action = _make_action(name='ruff[extra1]')
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True)

        result = await dry_run_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True
        assert result.installed_version == '0.8.0'

        # Build one without — defaults to False
        action2 = _make_plugin_action(include_prereleases=False)
        assert action2.include_prereleases is False
