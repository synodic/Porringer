"""Helpers for test update detection resolution.

Tests for the update-detection resolution primitives.

Covers ``is_package_installed`` 3-tuple return and ``check_for_newer_version``.
"""

from pathlib import Path

import pytest

from porringer.backend.command.core.resolution import (
    UpdateCheckError,
    check_for_newer_version,
    is_package_installed,
)
from porringer.core.plugin_schema.environment import CheckUpdatesParameters
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Package, PackageRef
from tests.unit.update_detection_helpers import make_env as _make_env


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
            env,
            PackageRef.model_validate('cppython'),
            '0.9.14',
            include_prereleases=False,
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
