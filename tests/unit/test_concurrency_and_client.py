"""Tests for concurrency limiting, shared httpx.AsyncClient, and console check command.

Validates:
1. ``max_concurrency`` field on ``SetupParameters``
2. Semaphore bounding in ``_dry_run_package_actions``
3. Shared ``httpx.AsyncClient`` is threaded through the update-check chain
4. Console ``check`` command properly awaits async methods
"""

import asyncio
import inspect
from pathlib import Path
from typing import override
from unittest.mock import MagicMock, patch

import httpx
from packaging.version import Version

from porringer.backend.command.core.execution import (
    _dry_run_package_actions,  # noqa: PLC2701
)
from porringer.backend.command.core.resolution import (
    ResolutionContext,
    check_for_newer_version,
)
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    PluginKind,
    PluginParameters,
)
from porringer.schema import SetupAction, SetupActionResult, SetupParameters

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


class _StubEnv(Environment):
    """Minimal environment stub for concurrency tests."""

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Return ecosystem identifier."""
        return Ecosystem('python')

    @classmethod
    @override
    def tool_name(cls) -> str | None:
        """Return tool name."""
        return 'stub'

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Return install command."""
        return ['stub', 'install', package.name]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Return uninstall command."""
        return ['stub', 'uninstall', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Return upgrade command."""
        return ['stub', 'upgrade', package.name]

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        """Return empty package list."""
        return [Package(name='existing', version='1.0.0')]

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Return no updates."""
        return []

    @classmethod
    @override
    def supports_parallel(cls) -> bool:
        """Support parallel execution."""
        return True


def _make_action(name: str, installer: str = 'stub') -> SetupAction:
    """Build a simple PACKAGE action for testing."""
    return SetupAction(
        description=f'Install {name}',
        kind=PluginKind.PACKAGE,
        ecosystem=Ecosystem('python'),
        installer=installer,
        package=PackageRef.model_validate(name),
    )


# =========================================================================
# CheckUpdatesParameters.http_client
# =========================================================================


class TestHttpClientField:
    """Tests for the ``http_client`` field on CheckUpdatesParameters."""

    @staticmethod
    def test_with_client() -> None:
        """Accepts an httpx.AsyncClient instance."""
        client = MagicMock(spec=httpx.AsyncClient)
        params = CheckUpdatesParameters(packages=[], http_client=client)
        assert params.http_client is client

    @staticmethod
    def test_excluded_from_serialization() -> None:
        """The http_client field is excluded from model dumps."""
        client = MagicMock(spec=httpx.AsyncClient)
        params = CheckUpdatesParameters(packages=[], http_client=client)
        dumped = params.model_dump()
        assert 'http_client' not in dumped


# =========================================================================
# ResolutionContext.http_client
# =========================================================================


class TestResolutionContextHttpClient:
    """Tests for the ``http_client`` field on ResolutionContext."""

    @staticmethod
    def test_with_client() -> None:
        """Accepts an httpx.AsyncClient instance."""
        client = MagicMock(spec=httpx.AsyncClient)
        ctx = ResolutionContext(http_client=client)
        assert ctx.http_client is client


# =========================================================================
# Semaphore bounding in dry-run
# =========================================================================


class TestDryRunConcurrencyBounding:
    """``_dry_run_package_actions`` respects ``max_concurrency``."""

    @staticmethod
    async def test_semaphore_limits_concurrency() -> None:
        """With max_concurrency=2, at most 2 tasks run concurrently."""
        environments: dict[str, Environment] = {'stub': _StubEnv(_MOCK_PARAMS)}
        max_concurrent = 2
        params = SetupParameters(dry_run=True, max_concurrency=max_concurrent)

        # Track peak concurrency
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def _counting_dry_run(action, envs, **kwargs):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            # Simulate some async work
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1
            return SetupActionResult(action=action, success=True, skipped=True, message='already installed')

        num_actions = 6
        actions = [_make_action(f'pkg-{i}') for i in range(num_actions)]

        with patch(
            'porringer.backend.command.core.execution.dry_run_action',
            side_effect=_counting_dry_run,
        ):
            results = await _dry_run_package_actions(
                actions,
                environments,
                asyncio.Queue(),
                parameters=params,
            )

        assert len(results) == num_actions
        assert all(r.success for r in results)
        assert peak <= max_concurrent, f'Peak concurrency was {peak}, expected <= {max_concurrent}'

    @staticmethod
    async def test_unlimited_concurrency() -> None:
        """With max_concurrency=0, all tasks can run simultaneously."""
        environments: dict[str, Environment] = {'stub': _StubEnv(_MOCK_PARAMS)}
        params = SetupParameters(dry_run=True, max_concurrency=0)

        call_count = 0

        async def _mock_dry_run(action, envs, **kwargs):
            nonlocal call_count
            call_count += 1
            return SetupActionResult(action=action, success=True, skipped=True, message='ok')

        num_actions = 5
        actions = [_make_action(f'pkg-{i}') for i in range(num_actions)]

        with patch(
            'porringer.backend.command.core.execution.dry_run_action',
            side_effect=_mock_dry_run,
        ):
            results = await _dry_run_package_actions(
                actions,
                environments,
                asyncio.Queue(),
                parameters=params,
            )

        assert len(results) == num_actions
        assert call_count == num_actions


# =========================================================================
# Shared httpx.AsyncClient threading
# =========================================================================


class TestSharedHttpClient:
    """Shared client is forwarded through the update-check chain."""

    @staticmethod
    async def test_check_for_newer_version_passes_client() -> None:
        """``check_for_newer_version`` includes http_client in params."""
        env = _StubEnv(_MOCK_PARAMS)
        client = MagicMock(spec=httpx.AsyncClient)

        captured_params: list[CheckUpdatesParameters] = []

        async def _spy_check_updates(params: CheckUpdatesParameters) -> list[Package]:
            captured_params.append(params)
            return [Package(name='ruff', version='0.9.0')]

        env.check_updates = _spy_check_updates  # type: ignore[assignment]

        result = await check_for_newer_version(
            env,
            PackageRef.model_validate('ruff'),
            '0.8.0',
            http_client=client,
        )
        assert result == '0.9.0'
        assert len(captured_params) == 1
        assert captured_params[0].http_client is client

    @staticmethod
    async def test_check_for_newer_version_none_client() -> None:
        """When no client is provided, http_client is None in params."""
        env = _StubEnv(_MOCK_PARAMS)

        captured_params: list[CheckUpdatesParameters] = []

        async def _spy_check_updates(params: CheckUpdatesParameters) -> list[Package]:
            captured_params.append(params)
            return [Package(name='ruff', version='0.9.0')]

        env.check_updates = _spy_check_updates  # type: ignore[assignment]

        await check_for_newer_version(
            env,
            PackageRef.model_validate('ruff'),
            '0.8.0',
        )
        assert captured_params[0].http_client is None

    @staticmethod
    async def test_dry_run_creates_shared_client() -> None:
        """Dry-run path creates a shared httpx.AsyncClient for all tasks."""
        environments: dict[str, Environment] = {'stub': _StubEnv(_MOCK_PARAMS)}
        params = SetupParameters(dry_run=True, max_concurrency=0)

        seen_clients: list[httpx.AsyncClient | None] = []

        async def _capture_client(action, envs, **kwargs):
            ctx = kwargs.get('context')
            seen_clients.append(ctx.http_client if ctx else None)
            return SetupActionResult(action=action, success=True, skipped=True, message='ok')

        num_actions = 3
        actions = [_make_action(f'pkg-{i}') for i in range(num_actions)]

        with patch(
            'porringer.backend.command.core.execution.dry_run_action',
            side_effect=_capture_client,
        ):
            await _dry_run_package_actions(
                actions,
                environments,
                asyncio.Queue(),
                parameters=params,
            )

        # All tasks should have received a non-None client
        assert len(seen_clients) == num_actions
        assert all(c is not None for c in seen_clients)
        # All tasks should share the same client instance
        assert seen_clients[0] is seen_clients[1] is seen_clients[2]


# =========================================================================
# Console check command fix
# =========================================================================


class TestConsoleCheckAsync:
    """Console check command properly awaits async plugin methods."""

    @staticmethod
    def test_check_plugin_updates_is_async() -> None:
        """``_check_plugin_updates`` is a coroutine function."""
        from porringer.console.command.check import (  # noqa: PLC0415
            _check_plugin_updates,  # noqa: PLC2701
        )

        # Verify _check_plugin_updates is a coroutine function
        assert inspect.iscoroutinefunction(_check_plugin_updates)
