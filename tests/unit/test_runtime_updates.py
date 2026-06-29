"""Helpers for test runtime updates.

Tests for per-runtime check_updates and per-action runtime_tag override.
"""

import asyncio
from pathlib import Path
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from packaging.version import Version

from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.execution import (
    execute_package,
    resolve_runtime_tag_override,
)
from porringer.backend.command.core.resolution import ResolutionContext
from porringer.backend.command.package import PackageCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import (
    ResolvedRuntime,
    RuntimeContext,
    RuntimeProvider,
)
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import (
    CheckParameters,
    SetupAction,
    Skip,
    SkipReason,
    SyncStrategy,
)

# Test constants
NUM_RUNTIMES_EXPECTED = 2

# ---------------------------------------------------------------------------
# Fake provider/consumer helpers (mirrors test_command_plugin.py pattern)
# ---------------------------------------------------------------------------


class _FakeProvider(Environment, RuntimeProvider):
    """Minimal RuntimeProvider for tests."""

    _distribution: Distribution

    def __init__(self, params: PluginParameters) -> None:
        self._distribution = params.distribution

    @staticmethod
    def ecosystem() -> Ecosystem | None:
        return Ecosystem('python')

    @staticmethod
    def plugin_kind() -> PluginKind:
        return PluginKind.RUNTIME

    @classmethod
    def provided_runtime_kind(cls) -> str:
        return 'python'

    @classmethod
    def tool_name(cls) -> str:
        return 'pim'

    @classmethod
    def is_available(cls) -> bool:
        return True

    @override
    async def resolve_executable(self, tag: str) -> Path | None:
        if tag == 'bad':
            return None
        return Path(f'/python/{tag}/python')

    @override
    async def available_tags(self) -> list[str]:
        return ['3.14', '3.12']

    @override
    def install_command(self, package, **kw):
        return []

    @override
    def upgrade_command(self, package, **kw):
        return []

    @override
    def uninstall_command(self, package, **kw):
        return []

    @override
    async def packages(self, **kw):
        return []

    @override
    async def check_updates(self, params):
        return []

    @staticmethod
    def dependencies() -> list:
        return []

    @property
    def distribution(self) -> Distribution:
        return self._distribution


def _provider() -> _FakeProvider:
    return _FakeProvider(PluginParameters(distribution=Distribution(version=Version('0.0.0'))))


# ===================================================================
# Feature 1: check_updates_by_runtime
# ===================================================================


class TestCheckUpdatesByRuntime:
    """PackageCommands.check_updates_by_runtime queries each runtime."""

    @staticmethod
    async def test_returns_results_per_runtime() -> None:
        """Each resolved runtime produces a RuntimeCheckResult entry."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')
        mock_env.is_supported = MagicMock(return_value=True)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(
            return_value=[
                Package(name='requests', version='2.28'),
            ]
        )
        mock_env.check_updates = AsyncMock(
            return_value=[
                Package(name='requests', version='2.31'),
            ]
        )

        discovered = DiscoveredPlugins(
            environments={'pip': mock_env},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/py/3.14')),
                ResolvedRuntime(provider='pim', tag='3.12', kind='python', executable=Path('/py/3.12')),
            ]

            results = await PackageCommands.check_updates_by_runtime(plugins=discovered)

        assert len(results) == NUM_RUNTIMES_EXPECTED
        assert results[0].provider == 'pim'
        assert results[0].tag == '3.14'
        assert results[1].tag == '3.12'
        # Each result should have a CheckResult for 'pip'
        assert len(results[0].results) == 1
        assert results[0].results[0].plugin == 'pip'

    @staticmethod
    async def test_skips_non_consumer_plugins() -> None:
        """Environments that are not RuntimeConsumer are excluded."""
        non_consumer = MagicMock(spec=Environment)
        # Environment doesn't implement RuntimeConsumer

        discovered = DiscoveredPlugins(
            environments={'brew': non_consumer},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
            return_value=[
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/py/3.14')),
            ],
        ):
            results = await PackageCommands.check_updates_by_runtime(plugins=discovered)

        assert results == []

    @staticmethod
    async def test_empty_runtimes_returns_empty() -> None:
        """When no runtimes are discovered, returns empty list."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')

        discovered = DiscoveredPlugins(
            environments={'pip': mock_env},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await PackageCommands.check_updates_by_runtime(plugins=discovered)

        assert results == []

    @staticmethod
    async def test_unavailable_runtime_excluded() -> None:
        """Runtimes where query_availability is False produce no result."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')
        mock_env.is_supported = MagicMock(return_value=True)
        mock_env.query_availability = MagicMock(return_value=False)

        discovered = DiscoveredPlugins(
            environments={'pip': mock_env},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/py/3.14')),
            ]

            results = await PackageCommands.check_updates_by_runtime(plugins=discovered)

        assert results == []

    @staticmethod
    async def test_plugin_filter_respected() -> None:
        """CheckParameters.plugins restricts which consumers are checked."""
        pip_env = MagicMock(spec=PythonEnvironment)
        pip_env.consumed_runtime_kind = MagicMock(return_value='python')
        pip_env.is_supported = MagicMock(return_value=True)
        pip_env.query_availability = MagicMock(return_value=True)
        pip_env.packages = AsyncMock(return_value=[])
        pip_env.check_updates = AsyncMock(return_value=[])

        uv_env = MagicMock(spec=PythonEnvironment)
        uv_env.consumed_runtime_kind = MagicMock(return_value='python')
        uv_env.is_supported = MagicMock(return_value=True)
        uv_env.query_availability = MagicMock(return_value=True)
        uv_env.packages = AsyncMock(return_value=[])
        uv_env.check_updates = AsyncMock(return_value=[])

        discovered = DiscoveredPlugins(
            environments={'pip': pip_env, 'uv': uv_env},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/py/3.14')),
            ]

            params = CheckParameters(plugins=['pip'])
            results = await PackageCommands.check_updates_by_runtime(params, plugins=discovered)

        # Only pip was checked, uv was filtered out
        assert len(results) == 1
        assert results[0].results[0].plugin == 'pip'
        uv_env.packages.assert_not_called()

    @staticmethod
    async def test_error_captured_in_check_result() -> None:
        """Exceptions during check_updates are captured as CheckResult errors."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')
        mock_env.is_supported = MagicMock(return_value=True)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=[])
        mock_env.check_updates = AsyncMock(side_effect=RuntimeError('network down'))

        discovered = DiscoveredPlugins(
            environments={'pip': mock_env},
            project_environments={},
            scm_environments={},
        )

        with patch.object(
            Builder,
            'resolve_all_runtime_executables',
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/py/3.14')),
            ]

            results = await PackageCommands.check_updates_by_runtime(plugins=discovered)

        assert len(results) == 1
        cr = results[0].results[0]
        assert cr.success is False
        assert cr.error is not None
        assert 'network down' in cr.error


# ===================================================================
# Feature 2: resolve_runtime_tag_override
# ===================================================================


class TestResolveRuntimeTagOverride:
    """resolve_runtime_tag_override builds a one-off RuntimeContext."""

    @staticmethod
    async def test_resolves_known_tag() -> None:
        """A resolvable tag produces a RuntimeContext with the executable."""
        provider = _provider()
        envs: dict[str, Environment] = {'pim': provider}

        ctx = await resolve_runtime_tag_override('3.14', 'python', envs)

        assert ctx is not None
        assert ctx.get('python') == Path('/python/3.14/python')

    @staticmethod
    async def test_unresolvable_tag_returns_none() -> None:
        """A tag that the provider cannot resolve returns None."""
        provider = _provider()
        envs: dict[str, Environment] = {'pim': provider}

        ctx = await resolve_runtime_tag_override('bad', 'python', envs)

        assert ctx is None

    @staticmethod
    async def test_no_matching_provider_returns_none() -> None:
        """When no provider matches the ecosystem, returns None."""
        provider = _provider()  # provides 'python'
        envs: dict[str, Environment] = {'pim': provider}

        ctx = await resolve_runtime_tag_override('3.14', 'node', envs)

        assert ctx is None

    @staticmethod
    async def test_none_ecosystem_returns_none() -> None:
        """When ecosystem is None the override is skipped."""
        provider = _provider()
        envs: dict[str, Environment] = {'pim': provider}

        ctx = await resolve_runtime_tag_override('3.14', None, envs)

        assert ctx is None

    @staticmethod
    async def test_non_provider_environment_skipped() -> None:
        """Environments that are not RuntimeProvider are skipped."""
        plain_env = MagicMock(spec=Environment)
        envs: dict[str, Environment] = {'brew': plain_env}

        ctx = await resolve_runtime_tag_override('3.14', 'python', envs)

        assert ctx is None


# ===================================================================
# Feature 2: execute_package with runtime_tag
# ===================================================================


class TestExecutePackageRuntimeTag:
    """execute_package honours SetupAction.runtime_tag."""

    @staticmethod
    async def test_runtime_tag_overrides_context() -> None:
        """When runtime_tag is set, the provider's executable is used."""
        provider = _provider()
        mock_env = MagicMock(spec=PythonEnvironment)

        envs: dict[str, Environment] = {'pip': mock_env, 'pim': provider}
        queue: asyncio.Queue = asyncio.Queue()

        action = SetupAction(
            description='Install numpy via pip',
            installer='pip',
            package=PackageRef(name='numpy', constraint='>=2.0'),
            ecosystem=Ecosystem('python'),
            runtime_tag='3.12',  # target a specific runtime
        )

        # Patch resolve_operation to verify ctx.runtime_context
        captured_ctx = {}

        async def _fake_resolve(act, envs_, strategy, ctx):
            captured_ctx['runtime'] = ctx.runtime_context
            # Return a skip so we don't need the full execution chain
            result = MagicMock()
            result.operation = Skip(reason=SkipReason.ALREADY_INSTALLED)
            result.message = 'already installed'
            result.action = act
            return result

        with patch(
            'porringer.backend.command.core.execution.resolve_operation',
            side_effect=_fake_resolve,
        ):
            result = await execute_package(action, envs, SyncStrategy.LATEST, queue)

        assert result.success is True
        # The runtime context should have been overridden
        rt = captured_ctx['runtime']
        assert rt.get('python') == Path('/python/3.12/python')

    @staticmethod
    async def test_runtime_tag_unresolvable_fails() -> None:
        """When runtime_tag can't be resolved, the action fails."""
        provider = _provider()
        envs: dict[str, Environment] = {'pip': MagicMock(spec=PythonEnvironment), 'pim': provider}
        queue: asyncio.Queue = asyncio.Queue()

        action = SetupAction(
            description='Install numpy via pip',
            installer='pip',
            package=PackageRef(name='numpy', constraint='>=2.0'),
            ecosystem=Ecosystem('python'),
            runtime_tag='bad',  # provider returns None for 'bad'
        )

        result = await execute_package(action, envs, SyncStrategy.LATEST, queue)

        assert result.success is False
        assert result.message is not None
        assert "Could not resolve runtime tag 'bad'" in result.message

    @staticmethod
    async def test_no_runtime_tag_uses_default_context() -> None:
        """Without runtime_tag, the phase-level context is used unchanged."""
        mock_env = MagicMock(spec=PythonEnvironment)
        envs: dict[str, Environment] = {'pip': mock_env}
        queue: asyncio.Queue = asyncio.Queue()

        phase_ctx = RuntimeContext(executables={'python': Path('/default/python')})
        context = ResolutionContext(runtime_context=phase_ctx)

        action = SetupAction(
            description='Install numpy via pip',
            installer='pip',
            package=PackageRef(name='numpy', constraint='>=2.0'),
            ecosystem=Ecosystem('python'),
            # runtime_tag is None (default)
        )

        captured_ctx = {}

        async def _fake_resolve(act, envs_, strategy, ctx):
            captured_ctx['runtime'] = ctx.runtime_context
            result = MagicMock()
            result.operation = Skip(reason=SkipReason.ALREADY_INSTALLED)
            result.message = 'already installed'
            result.action = act
            return result

        with patch(
            'porringer.backend.command.core.execution.resolve_operation',
            side_effect=_fake_resolve,
        ):
            result = await execute_package(action, envs, SyncStrategy.LATEST, queue, context)

        assert result.success is True
        # Default context preserved
        assert captured_ctx['runtime'].get('python') == Path('/default/python')
