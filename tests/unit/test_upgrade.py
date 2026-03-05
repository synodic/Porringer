"""Tests for the imperative package upgrade feature (API.upgrade).

Covers:
- execute_package with SyncStrategy.LATEST (upgrade vs install routing)
- API.upgrade() plugin validation, dry-run, and runtime_tag threading
- API.upgrade() auto-resolves runtime_context from plugins
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import execute_package
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import LocalConfiguration, SetupAction, SetupActionResult, SyncStrategy
from porringer.test.mock.environment import MockEnvironment

_PY = Ecosystem('python')
_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


def _make_plugins(
    environments: dict[str, Environment] | None = None,
    project_environments: dict[str, ProjectEnvironment] | None = None,
) -> DiscoveredPlugins:
    return DiscoveredPlugins(
        environments=environments or {},
        project_environments=project_environments or {},
        scm_environments={},
    )


def _make_action(
    *,
    package: str = 'requests',
    installer: str = 'mock',
    runtime_tag: str | None = None,
) -> SetupAction:
    return SetupAction(
        description=f"Upgrade '{package}'",
        kind=PluginKind.PACKAGE,
        ecosystem=_PY,
        installer=installer,
        package=PackageRef.model_validate(package),
        runtime_tag=runtime_tag,
    )


def _make_mock_env(*, installed: list[Package] | None = None) -> MockEnvironment:
    """Create a MockEnvironment with configurable packages()."""
    env = MockEnvironment(_MOCK_PARAMS)
    pkgs = installed or []

    async def _packages(*, project_path: Path | None = None, runtime_context: object = None) -> list[Package]:
        del project_path, runtime_context
        return pkgs

    env.packages = _packages  # type: ignore[assignment]
    return env


# ---------------------------------------------------------------------------
# execute_package with SyncStrategy.LATEST
# ---------------------------------------------------------------------------


class TestExecutePackageUpgrade:
    """Test that execute_package routes to upgrade for installed packages."""

    @staticmethod
    async def test_upgrades_installed_package() -> None:
        """execute_package with LATEST calls upgrade on installed package."""
        env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        env.upgrade = AsyncMock(return_value=Package(name='requests', version='2.32.0'))  # type: ignore[assignment]

        # check_updates must report a newer version so resolve_operation
        # produces an Upgrade rather than Skip(ALREADY_LATEST).
        async def _check_updates(params):
            return [Package(name='requests', version='2.32.0')]

        env.check_updates = _check_updates  # type: ignore[assignment]
        envs: dict[str, Environment] = {'mock': env}

        action = _make_action()
        result = await execute_package(action, envs, SyncStrategy.LATEST, asyncio.Queue())
        assert result.success is True
        env.upgrade.assert_awaited_once()

    @staticmethod
    async def test_installs_missing_package() -> None:
        """execute_package with LATEST installs when package is not present."""
        env = _make_mock_env(installed=[])
        env.install = AsyncMock(return_value=Package(name='requests', version='2.32.0'))  # type: ignore[assignment]
        envs: dict[str, Environment] = {'mock': env}

        action = _make_action()
        result = await execute_package(action, envs, SyncStrategy.LATEST, asyncio.Queue())
        assert result.success is True
        env.install.assert_awaited_once()

    @staticmethod
    async def test_fails_when_installer_missing() -> None:
        """execute_package fails when installer is not specified."""
        action = SetupAction(
            description='Bad action',
            kind=PluginKind.TOOL,
            ecosystem=_PY,
            installer=None,
            package=None,
        )
        result = await execute_package(action, {}, SyncStrategy.LATEST, asyncio.Queue())
        assert result.success is False

    @staticmethod
    async def test_runtime_tag_flows_through() -> None:
        """execute_package respects runtime_tag on the action."""
        env = _make_mock_env(installed=[])
        env.install = AsyncMock(return_value=Package(name='requests', version='2.32.0'))  # type: ignore[assignment]
        envs: dict[str, Environment] = {'mock': env}

        action = _make_action(runtime_tag='3.12')
        assert action.runtime_tag == '3.12'
        # The action carries the tag; execute_package will attempt to resolve it.
        # Without a matching runtime provider, it should return an error.
        result = await execute_package(action, envs, SyncStrategy.LATEST, asyncio.Queue())
        # runtime_tag resolution will fail since no runtime provider is available
        assert result.success is False
        assert 'runtime tag' in (result.message or '').lower()


# ---------------------------------------------------------------------------
# API.upgrade — plugin validation
# ---------------------------------------------------------------------------


class TestUpgradePluginValidation:
    """Test that API.upgrade() validates plugin availability."""

    @staticmethod
    async def test_unknown_plugin_returns_error() -> None:
        """API.upgrade with unknown plugin returns failure result."""
        plugins = _make_plugins()

        with patch('porringer.api.discover_all_plugins', return_value=plugins):
            config = LocalConfiguration()
            api = API(config)
            result = await api.upgrade(
                'nonexistent',
                PackageRef.model_validate('requests'),
                plugins=plugins,
            )

        assert result.success is False
        assert 'not available' in (result.message or '')


# ---------------------------------------------------------------------------
# API.upgrade — dry run
# ---------------------------------------------------------------------------


class TestUpgradeDryRun:
    """Test that API.upgrade() dry_run resolves without executing."""

    @staticmethod
    async def test_dry_run_installed_package() -> None:
        """dry_run=True resolves operation but does not execute."""
        env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        env.upgrade = AsyncMock()  # type: ignore[assignment]
        envs: dict[str, Environment] = {'mock': env}
        plugins = _make_plugins(environments=envs)

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(type(env), 'ecosystem', return_value=_PY),
            patch.object(type(env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.upgrade(
                'mock',
                PackageRef.model_validate('requests'),
                plugins=plugins,
                dry_run=True,
            )

        # Should not have called the actual upgrade method
        env.upgrade.assert_not_awaited()
        assert result is not None

    @staticmethod
    async def test_dry_run_missing_package() -> None:
        """dry_run=True on missing package reports install-instead."""
        env = _make_mock_env(installed=[])
        envs: dict[str, Environment] = {'mock': env}
        plugins = _make_plugins(environments=envs)

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(type(env), 'ecosystem', return_value=_PY),
            patch.object(type(env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.upgrade(
                'mock',
                PackageRef.model_validate('requests'),
                plugins=plugins,
                dry_run=True,
            )

        assert result is not None
        # Resolved as Install (not installed → will install under LATEST)
        assert not result.skipped


# ---------------------------------------------------------------------------
# API.upgrade — runtime context auto-resolution
# ---------------------------------------------------------------------------


class TestUpgradeAutoResolveRuntimeContext:
    """API.upgrade() auto-resolves RuntimeContext when none is provided."""

    @staticmethod
    async def test_auto_resolves_when_none() -> None:
        """When runtime_context=None, Builder.resolve_runtime_context is called."""
        ctx = RuntimeContext(executables={'python': Path('/fake/python')})
        mock_env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])

        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx) as mock_resolve,
            patch.object(type(mock_env), 'ecosystem', return_value=_PY),
            patch.object(type(mock_env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.upgrade('mock', PackageRef.model_validate('requests'), dry_run=True)

        mock_resolve.assert_called_once()
        assert result is not None

    @staticmethod
    async def test_explicit_context_skips_auto_resolve() -> None:
        """When runtime_context is provided, Builder.resolve_runtime_context is NOT called."""
        ctx = RuntimeContext(executables={'python': Path('/explicit/python')})
        mock_env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])

        plugins = DiscoveredPlugins(
            environments={'mock': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
            patch.object(type(mock_env), 'ecosystem', return_value=_PY),
            patch.object(type(mock_env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            result = await api.upgrade(
                'mock',
                PackageRef.model_validate('requests'),
                runtime_context=ctx,
                dry_run=True,
            )

        mock_resolve.assert_not_called()
        assert result is not None


# ---------------------------------------------------------------------------
# API.upgrade — runtime_tag parameter
# ---------------------------------------------------------------------------


class TestUpgradeRuntimeTag:
    """Test that API.upgrade() threads runtime_tag through to the action."""

    @staticmethod
    async def test_runtime_tag_set_on_action() -> None:
        """runtime_tag kwarg is set on the constructed SetupAction."""
        env = _make_mock_env(installed=[Package(name='requests', version='2.31.0')])
        envs: dict[str, Environment] = {'mock': env}
        plugins = _make_plugins(environments=envs)

        # Capture the action passed to execute_package
        captured_actions: list[SetupAction] = []

        async def _capture_execute(action, *args, **kwargs):
            captured_actions.append(action)
            return SetupActionResult(action=action, success=True, message='captured')

        with (
            patch('porringer.api.execute_package', side_effect=_capture_execute),
            patch('porringer.api.discover_all_plugins', return_value=plugins),
            patch.object(type(env), 'ecosystem', return_value=_PY),
            patch.object(type(env), 'plugin_kind', return_value=PluginKind.PACKAGE),
        ):
            config = LocalConfiguration()
            api = API(config)
            await api.upgrade(
                'mock',
                PackageRef.model_validate('requests'),
                runtime_tag='3.11',
                plugins=plugins,
            )

        assert len(captured_actions) == 1
        assert captured_actions[0].runtime_tag == '3.11'
