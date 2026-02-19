"""Tests for update detection during dry-run.

Covers the new ``SkipReason.UPDATE_AVAILABLE`` path, version fields
on ``SetupActionResult``, and the ``detect_updates`` /
``include_prereleases`` flags on ``SetupParameters``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from porringer.backend.command.core.presence import (
    _check_for_newer_version,  # noqa: PLC2701
    _dry_run_package_action,  # noqa: PLC2701
    dry_run_action,
    is_package_installed,
)
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SkipReason,
)

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
        pkg = PackageRef.model_validate('ruff')
        installed = Package(name='ruff', version='0.8.0')
        ok, detail, matched = is_package_installed(pkg, [installed], 'pep440')
        assert ok is True
        assert matched is installed
        assert detail is not None

    @staticmethod
    def test_not_installed_returns_none() -> None:
        pkg = PackageRef.model_validate('ruff')
        ok, detail, matched = is_package_installed(pkg, [], 'pep440')
        assert ok is False
        assert matched is None
        assert detail is None


# ---------------------------------------------------------------------------
# _check_for_newer_version
# ---------------------------------------------------------------------------


class TestCheckForNewerVersion:
    """Unit tests for the helper that queries a plugin for updates."""

    @staticmethod
    def test_returns_newer_version() -> None:
        env = _make_env(updates=[Package(name='ruff', version='0.9.0')])
        result = _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result == '0.9.0'

    @staticmethod
    def test_returns_none_when_up_to_date() -> None:
        env = _make_env(updates=[Package(name='ruff', version='0.8.0')])
        result = _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result is None

    @staticmethod
    def test_returns_none_when_plugin_has_no_updates() -> None:
        env = _make_env(updates=[])
        result = _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result is None

    @staticmethod
    def test_returns_none_when_plugin_raises() -> None:
        env = _make_env()
        env.check_updates.side_effect = RuntimeError('boom')
        result = _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0')
        assert result is None

    @staticmethod
    def test_forwards_include_prereleases() -> None:
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True)
        call_args = env.check_updates.call_args
        params: CheckUpdatesParameters = call_args[0][0]
        assert params.include_prereleases is True

    @staticmethod
    def test_returns_newer_prerelease() -> None:
        env = _make_env(updates=[Package(name='ruff', version='0.9.0a1')])
        result = _check_for_newer_version(env, PackageRef.model_validate('ruff'), '0.8.0', include_prereleases=True)
        assert result == '0.9.0a1'


# ---------------------------------------------------------------------------
# _dry_run_package_action — UPDATE_AVAILABLE path
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

        result = _dry_run_package_action(action, envs, parameters=params)

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

        result = _dry_run_package_action(action, envs, parameters=params)

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

        result = _dry_run_package_action(action, envs, parameters=params)

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

        result = _dry_run_package_action(action, envs)

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

        result = _dry_run_package_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    def test_include_prereleases_forwarded() -> None:
        """include_prereleases flag is threaded to the check_updates call."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],
        )
        envs = {'pip': env}
        params = SetupParameters(dry_run=True, detect_updates=True, include_prereleases=True)

        _dry_run_package_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True

    @staticmethod
    def test_per_package_prereleases_overrides_global() -> None:
        """Per-action include_prereleases=True is combined with global flag."""
        action = _make_action()
        action.include_prereleases = True
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}
        # Global is False, but per-package is True → prereleases included
        params = SetupParameters(dry_run=True, detect_updates=True, include_prereleases=False)

        _dry_run_package_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True


# ---------------------------------------------------------------------------
# dry_run_action — top-level dispatch
# ---------------------------------------------------------------------------


class TestDryRunActionDispatch:
    """Verify the top-level dry_run_action passes parameters through."""

    @staticmethod
    def test_parameters_threaded_to_package_action() -> None:
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
        params = SetupParameters()
        assert params.detect_updates is False
        assert params.include_prereleases is False

    @staticmethod
    def test_explicit_values() -> None:
        params = SetupParameters(detect_updates=True, include_prereleases=True)
        assert params.detect_updates is True
        assert params.include_prereleases is True


# ---------------------------------------------------------------------------
# SetupActionResult version fields
# ---------------------------------------------------------------------------


class TestSetupActionResultVersionFields:
    """Verify the new version fields on the result dataclass."""

    @staticmethod
    def test_defaults_are_none() -> None:
        action = _make_action()
        result = SetupActionResult(action=action, success=True)
        assert result.installed_version is None
        assert result.available_version is None

    @staticmethod
    def test_explicit_values() -> None:
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
            'porringer.backend.command.core.presence.find_plugin_manager',
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
            'porringer.backend.command.core.presence.find_plugin_manager',
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
            'porringer.backend.command.core.presence.find_plugin_manager',
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

        params = SetupParameters(dry_run=True, detect_updates=True, include_prereleases=False)

        with patch(
            'porringer.backend.command.core.presence.find_plugin_manager',
            return_value=manager,
        ):
            result = dry_run_action(action, envs, parameters=params)

        # Per-package flag overrides global False
        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
