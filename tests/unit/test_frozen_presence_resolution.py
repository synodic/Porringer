"""Tests for frozen-app presence detection, version probing, and resolution.

Covers:
- ``probe_tool_version()`` subprocess parsing
- PATH fallback in frozen (PyInstaller) environments
- ``resolved_to_result()`` version metadata forwarding (Install, Upgrade, Uninstall)
- ``forward_version_metadata()`` in execution.py
- Frozen vs. non-frozen resolution matrix
"""

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from porringer.backend.command.core.execution import forward_version_metadata
from porringer.backend.command.core.resolution import (
    ResolutionContext,
    ResolvedOperation,
    probe_tool_version,
    resolve_operation,
    resolved_to_result,
)
from porringer.core.schema import Package, PackageRef, PluginKind
from porringer.schema import (
    Install,
    SetupAction,
    SetupActionResult,
    Skip,
    SkipReason,
    SyncStrategy,
    Uninstall,
    Upgrade,
)
from porringer.test.mock.subprocess import fake_proc
from tests.conftest import environment_mode

_RESOLUTION_MODULE = 'porringer.backend.command.core.resolution'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_env(packages: list[Any] | None = None) -> MagicMock:
    """Build a minimal mock ``Environment`` with the given installed packages."""
    env = MagicMock()
    env.packages = AsyncMock(return_value=packages if packages is not None else [])
    env.package_name_validator = MagicMock(return_value=None)
    type(env).package_name_validator = classmethod(lambda cls: None)
    return env


def _make_action(
    name: str = 'pipx',
    installer: str = 'pip',
    kind: PluginKind = PluginKind.PACKAGE,
    constraint: str | None = None,
) -> SetupAction:
    """Build a minimal ``SetupAction`` for testing."""
    return SetupAction(
        description=f'Install {name}',
        kind=kind,
        installer=installer,
        package=PackageRef(name=name, constraint=constraint),
    )


# ---------------------------------------------------------------------------
# probe_tool_version
# ---------------------------------------------------------------------------


class TestProbeToolVersion:
    """Unit tests for the ``probe_tool_version`` helper."""

    @staticmethod
    async def test_parses_semver_from_stdout() -> None:
        """Standard ``<name> 1.7.1`` output yields ``'1.7.1'``."""
        proc = fake_proc(stdout='pipx 1.7.1')
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            result = await probe_tool_version('pipx')
        assert result == '1.7.1'

    @staticmethod
    async def test_parses_version_with_v_prefix() -> None:
        """A leading ``v`` prefix is stripped (e.g. ``v2.0.3``)."""
        proc = fake_proc(stdout='v2.0.3')
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            result = await probe_tool_version('deno')
        assert result == '2.0.3'

    @staticmethod
    async def test_parses_two_component_version() -> None:
        """Two-component versions like ``2.45`` are recognised."""
        proc = fake_proc(stdout='git version 2.45')
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            result = await probe_tool_version('git')
        assert result == '2.45'

    @staticmethod
    async def test_parses_version_from_stderr() -> None:
        """Version printed to stderr is still captured."""
        proc = fake_proc(stderr='tool 3.12.0')
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            result = await probe_tool_version('tool')
        assert result == '3.12.0'

    @staticmethod
    async def test_returns_none_for_no_version_in_output() -> None:
        """Output without a parseable version yields ``None``."""
        proc = fake_proc(stdout='no version here')
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            result = await probe_tool_version('mystery')
        assert result is None

    @staticmethod
    async def test_returns_none_on_file_not_found() -> None:
        """Binary not found on PATH yields ``None``."""
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            result = await probe_tool_version('missing')
        assert result is None

    @staticmethod
    async def test_returns_none_on_os_error() -> None:
        """General OSError yields ``None``."""
        with patch('asyncio.create_subprocess_exec', side_effect=OSError('broken')):
            result = await probe_tool_version('broken')
        assert result is None

    @staticmethod
    async def test_returns_none_on_timeout() -> None:
        """Subprocess exceeding the timeout yields ``None``."""
        proc = fake_proc()
        # Use a regular MagicMock so that calling communicate() does not
        # create a coroutine object.  The patched asyncio.wait_for raises
        # `TimeoutError synchronously.
        proc.communicate = MagicMock()
        with (
            patch('asyncio.create_subprocess_exec', return_value=proc),
            patch('asyncio.wait_for', side_effect=TimeoutError),
        ):
            result = await probe_tool_version('slow')
        assert result is None


# ---------------------------------------------------------------------------
# PATH fallback (frozen context)
# ---------------------------------------------------------------------------


class TestPathFallbackFrozenResolution:
    """PATH fallback in frozen environments when the package list misses a tool."""

    @staticmethod
    async def test_frozen_path_fallback_marks_installed_with_version() -> None:
        """Tool on PATH with a parseable version resolves to Skip(ALREADY_INSTALLED)."""
        action = _make_action('pipx')
        mock_env = _make_mock_env()

        proc = fake_proc(stdout='pipx 1.7.1')

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch(f'{_RESOLUTION_MODULE}.shutil.which', return_value='/usr/bin/pipx'),
            patch('asyncio.create_subprocess_exec', return_value=proc),
        ):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED
        assert resolved.operation.installed_version == '1.7.1'

    @staticmethod
    async def test_frozen_path_fallback_no_version_suppresses_update() -> None:
        """Tool on PATH but unparseable version suppresses update detection."""
        action = _make_action('custom-tool')
        mock_env = _make_mock_env()

        proc = fake_proc(stdout='no version here')

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch(f'{_RESOLUTION_MODULE}.shutil.which', return_value='/usr/bin/custom-tool'),
            patch('asyncio.create_subprocess_exec', return_value=proc),
        ):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.reason == SkipReason.ALREADY_INSTALLED
        # No version → no update available
        assert resolved.operation.available_version is None

    @staticmethod
    async def test_non_frozen_does_not_use_path_fallback() -> None:
        """Outside a frozen app a missing package stays not-installed."""
        action = _make_action('pipx')
        mock_env = _make_mock_env()

        # Ensure sys.frozen is absent (normal environment)
        with (
            patch.object(sys, 'frozen', False, create=True),
            patch(f'{_RESOLUTION_MODULE}.shutil.which', return_value='/usr/bin/pipx'),
        ):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Install)


# ---------------------------------------------------------------------------
# resolved_to_result — version forwarding
# ---------------------------------------------------------------------------


class TestResolvedToResultVersionMetadata:
    """``resolved_to_result`` must carry version metadata for all operation types."""

    @staticmethod
    def test_install_carries_available_version() -> None:
        """Install result surfaces the manifest constraint."""
        action = _make_action('ruff', constraint='>=0.8.0')
        resolved = ResolvedOperation(
            action=action,
            operation=Install(available_version='>=0.8.0'),
        )
        result = resolved_to_result(resolved)
        assert result.available_version == '>=0.8.0'
        assert result.installed_version is None

    @staticmethod
    def test_upgrade_carries_both_versions() -> None:
        """Upgrade result surfaces both installed and available versions."""
        action = _make_action('ruff')
        resolved = ResolvedOperation(
            action=action,
            operation=Upgrade(installed_version='0.7.0', available_version='0.8.0'),
        )
        result = resolved_to_result(resolved)
        assert result.installed_version == '0.7.0'
        assert result.available_version == '0.8.0'

    @staticmethod
    def test_uninstall_carries_installed_version() -> None:
        """Uninstall result surfaces the installed version."""
        action = _make_action('ruff')
        resolved = ResolvedOperation(
            action=action,
            operation=Uninstall(installed_version='0.7.0'),
        )
        result = resolved_to_result(resolved)
        assert result.installed_version == '0.7.0'
        assert result.available_version is None

    @staticmethod
    def test_skip_update_available_carries_both() -> None:
        """Skip with UPDATE_AVAILABLE surfaces both versions."""
        action = _make_action('ruff')
        resolved = ResolvedOperation(
            action=action,
            operation=Skip(
                reason=SkipReason.UPDATE_AVAILABLE,
                installed_version='0.7.0',
                available_version='0.8.0',
            ),
        )
        result = resolved_to_result(resolved)
        assert result.installed_version == '0.7.0'
        assert result.available_version == '0.8.0'
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE


# ---------------------------------------------------------------------------
# forward_version_metadata (execution.py)
# ---------------------------------------------------------------------------


class TestForwardVersionMetadata:
    """``forward_version_metadata`` merges resolved version info into a bare result."""

    @staticmethod
    def test_install_forwards_available_version() -> None:
        """Install operation copies ``available_version`` onto the result."""
        action = _make_action('ruff', constraint='>=0.8.0')
        result = SetupActionResult(action=action, success=True, message='Installed ruff')
        operation = Install(available_version='>=0.8.0')

        forward_version_metadata(result, operation)

        assert result.available_version == '>=0.8.0'

    @staticmethod
    def test_upgrade_forwards_both_versions() -> None:
        """Upgrade operation copies both version fields."""
        action = _make_action('ruff')
        result = SetupActionResult(action=action, success=True, message='Upgraded ruff')
        operation = Upgrade(installed_version='0.7.0', available_version='0.8.0')

        forward_version_metadata(result, operation)

        assert result.installed_version == '0.7.0'
        assert result.available_version == '0.8.0'

    @staticmethod
    def test_uninstall_forwards_installed_version() -> None:
        """Uninstall operation copies ``installed_version``."""
        action = _make_action('ruff')
        result = SetupActionResult(action=action, success=True, message='Uninstalled ruff')
        operation = Uninstall(installed_version='0.7.0')

        forward_version_metadata(result, operation)

        assert result.installed_version == '0.7.0'
        assert result.available_version is None

    @staticmethod
    def test_does_not_overwrite_existing_version_on_result() -> None:
        """Pre-existing version fields on the result are preserved."""
        action = _make_action('ruff')
        result = SetupActionResult(
            action=action, success=True, message='ok', installed_version='1.0.0', available_version='2.0.0'
        )
        operation = Install(installed_version='0.5.0', available_version='0.6.0')

        forward_version_metadata(result, operation)

        # Existing values preserved
        assert result.installed_version == '1.0.0'
        assert result.available_version == '2.0.0'

    @staticmethod
    def test_skip_operation_is_noop() -> None:
        """Skip operations are not Install/Upgrade/Uninstall — nothing forwarded."""
        action = _make_action('ruff')
        result = SetupActionResult(action=action, success=True, message='skipped')
        operation = Skip(reason=SkipReason.ALREADY_INSTALLED, installed_version='1.0.0')

        forward_version_metadata(result, operation)

        assert result.installed_version is None
        assert result.available_version is None


# ---------------------------------------------------------------------------
# Frozen / non-frozen resolution matrix
# ---------------------------------------------------------------------------


@environment_mode
class TestResolutionEnvironmentMatrix:
    """Parametrized frozen vs. normal resolution behaviours."""

    @staticmethod
    async def test_installed_package_skips_in_both_modes(is_frozen: bool) -> None:
        """A present package is always skipped under MINIMAL, regardless of frozen state."""
        action = _make_action('pipx')
        mock_env = _make_mock_env([Package(name='pipx', version='1.7.0')])

        # Suppress update check for simplicity
        mock_env.check_updates = AsyncMock(return_value=None)

        patches: list[Any] = []
        if is_frozen:
            patches.append(patch.object(sys, 'frozen', True, create=True))
            patches.append(patch.object(sys, 'executable', r'C:\app\synodic.exe'))
        else:
            patches.append(patch.object(sys, 'frozen', False, create=True))

        for p in patches:
            p.start()
        try:
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )
        finally:
            for p in reversed(patches):
                p.stop()

        assert isinstance(resolved.operation, Skip)
        assert resolved.operation.installed_version == '1.7.0'

    @staticmethod
    async def test_missing_package_installs_in_normal_mode(is_frozen: bool) -> None:
        """A missing package triggers Install when not rescued by PATH fallback."""
        action = _make_action('ruff')
        mock_env = _make_mock_env()

        patches: list[Any] = []
        if is_frozen:
            patches.append(patch.object(sys, 'frozen', True, create=True))
            patches.append(patch.object(sys, 'executable', r'C:\app\synodic.exe'))
            # Not on PATH either
            patches.append(patch(f'{_RESOLUTION_MODULE}.shutil.which', return_value=None))
        else:
            patches.append(patch.object(sys, 'frozen', False, create=True))

        for p in patches:
            p.start()
        try:
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )
        finally:
            for p in reversed(patches):
                p.stop()

        assert isinstance(resolved.operation, Install)


# ---------------------------------------------------------------------------
# Install constraint forwarding through _apply_strategy
# ---------------------------------------------------------------------------


class TestInstallConstraintPopulation:
    """Install operations carry the manifest constraint as ``available_version``."""

    @staticmethod
    async def test_minimal_not_installed_carries_constraint() -> None:
        """MINIMAL strategy populates ``available_version`` from the constraint."""
        action = _make_action('ruff', constraint='>=0.8.0')
        mock_env = _make_mock_env()

        with patch.object(sys, 'frozen', False, create=True):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Install)
        assert resolved.operation.available_version == '>=0.8.0'

    @staticmethod
    async def test_latest_not_installed_carries_constraint() -> None:
        """LATEST strategy populates ``available_version`` from the constraint."""
        action = _make_action('ruff', constraint='>=0.8.0')
        mock_env = _make_mock_env()

        with patch.object(sys, 'frozen', False, create=True):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.LATEST,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Install)
        assert resolved.operation.available_version == '>=0.8.0'

    @staticmethod
    async def test_no_constraint_yields_none() -> None:
        """A bare package name without constraint yields ``None``."""
        action = _make_action('ruff', constraint=None)
        mock_env = _make_mock_env()

        with patch.object(sys, 'frozen', False, create=True):
            resolved = await resolve_operation(
                action,
                {'pip': mock_env},
                SyncStrategy.MINIMAL,
                ResolutionContext(),
            )

        assert isinstance(resolved.operation, Install)
        assert resolved.operation.available_version is None
