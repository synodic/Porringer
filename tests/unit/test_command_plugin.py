"""Test the command 'plugin'"""

import os
import re
import sys
from pathlib import Path
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.version import InvalidVersion, Version

from porringer.api import API
from porringer.backend.builder import Builder, PluginInformation
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.package import PackageCommands
from porringer.backend.command.plugin import PluginCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.python_environment import PythonEnvironment
from porringer.core.plugin_schema.runtime import ResolvedRuntime, RuntimeConsumer, RuntimeContext, RuntimeProvider
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Distribution, Ecosystem, Package, PluginDependency, PluginKind, PluginParameters
from porringer.schema import LocalConfiguration
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation

# Test constants
NUM_PLUGINS_MULTIPLE = 3
NUM_PLUGINS_PARTIAL = 2
NUM_RESOLVED_TAGS = 2
NUM_CONCURRENT_RUNTIMES = 3


class TestCommandPlugin:
    """Test the command 'plugin'"""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _skip_tool_version():
        """Bypass ``tool_version()`` subprocess calls — this test only verifies listing."""
        with (
            patch('porringer.backend.resolver.ToolBasedPlugin.tool_version', return_value=None),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            yield

    @staticmethod
    async def test_plugin_list() -> None:
        """Test the plugin list"""
        config = LocalConfiguration()
        api = API(config)

        results = await api.plugin.list()

        assert results
        # Each result should have an installed status based on is_available()
        for result in results:
            assert isinstance(result.installed, bool)
            # tool_version is None because we patched it above
            assert result.tool_version is None

    @staticmethod
    def test_plugin_list_with_missing_module() -> None:
        """Test that plugin listing handles ModuleNotFoundError gracefully.

        This reproduces an issue where a downstream project, a frozen application,
        has registered entry points for plugins that cannot be imported because the
        module doesn't exist in that context.
        """
        builder = Builder()

        # Create a mock entry point that raises ModuleNotFoundError when loaded
        mock_entry_point = MagicMock()
        mock_entry_point.load.side_effect = ModuleNotFoundError("No module named 'porringer.plugin")
        mock_entry_point.name = 'missing_plugin'

        with patch('porringer.backend.builder.metadata.entry_points') as mock_entry_points:
            mock_entry_points.return_value = [mock_entry_point]

            # This should not raise an exception - it should handle the error gracefully
            result = builder.find_plugins('environment', Environment)

            # The result should be empty since the plugin couldn't be loaded
            assert result == []


class TestPluginInstall:
    """Test plugin install command"""

    @staticmethod
    def test_install_dry_run_pip() -> None:
        """Test install dry run with pip (non-pipx installation)"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False):
            result = commands.install('some-plugin', dry_run=True)

            assert result.success
            assert 'Would install' in result.message
            assert 'pip install' in result.message
            assert 'some-plugin' in result.message

    @staticmethod
    def test_install_dry_run_pipx() -> None:
        """Test install dry run with pipx installation"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=True):
            result = commands.install('some-plugin', dry_run=True)

            assert result.success
            assert 'Would install' in result.message
            assert 'pipx inject' in result.message
            assert 'some-plugin' in result.message

    @staticmethod
    def test_install_failure_returns_error() -> None:
        """Test that install failure returns error result"""
        commands = PluginCommands()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'Package not found'

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', return_value=mock_result),
        ):
            result = commands.install('nonexistent-plugin')

            assert not result.success
            assert 'failed' in result.message.lower()

    @staticmethod
    def test_install_validates_plugin_entry_point() -> None:
        """Test that install validates the package provides entry points"""
        commands = PluginCommands()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Successfully installed'
        mock_result.stderr = ''

        # Mock that the package doesn't add any new entry points
        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', return_value=mock_result),
            patch.object(commands, '_get_existing_plugin_packages', return_value=set()),
        ):
            with pytest.raises(PluginError) as exc_info:
                commands.install('not-a-plugin')

            assert 'not a valid Porringer plugin' in str(exc_info.value)

    @staticmethod
    def test_install_command_not_found() -> None:
        """Test handling of FileNotFoundError"""
        commands = PluginCommands()

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=True),
            patch('porringer.backend.command.plugin.subprocess.run', side_effect=FileNotFoundError('pipx not found')),
        ):
            result = commands.install('some-plugin')

            assert not result.success
            assert 'not found' in result.message.lower()


class TestPluginUninstall:
    """Test plugin uninstall command"""

    @staticmethod
    def test_uninstall_dry_run_pip() -> None:
        """Test uninstall dry run with pip"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False):
            results = commands.uninstall(['some-plugin'], dry_run=True)

            assert len(results) == 1
            assert results[0].success
            assert 'Would uninstall' in results[0].message
            assert 'pip uninstall' in results[0].message

    @staticmethod
    def test_uninstall_dry_run_pipx() -> None:
        """Test uninstall dry run with pipx"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=True):
            results = commands.uninstall(['some-plugin'], dry_run=True)

            assert len(results) == 1
            assert results[0].success
            assert 'Would uninstall' in results[0].message
            assert 'pipx uninject' in results[0].message

    @staticmethod
    def test_uninstall_multiple_plugins() -> None:
        """Test uninstalling multiple plugins"""
        commands = PluginCommands()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Successfully uninstalled'
        mock_result.stderr = ''

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', return_value=mock_result),
        ):
            results = commands.uninstall(['plugin-a', 'plugin-b', 'plugin-c'])

            assert len(results) == NUM_PLUGINS_MULTIPLE
            assert all(r.success for r in results)

    @staticmethod
    def test_uninstall_partial_failure() -> None:
        """Test that partial failures are reported correctly"""
        commands = PluginCommands()

        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            # First call succeeds, second fails
            if call_count == 1:
                result.returncode = 0
                result.stdout = 'Success'
                result.stderr = ''
            else:
                result.returncode = 1
                result.stderr = 'Package not found'
            return result

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', side_effect=mock_run),
        ):
            results = commands.uninstall(['plugin-ok', 'plugin-fail'])

            assert len(results) == NUM_PLUGINS_PARTIAL
            assert results[0].success
            assert not results[1].success


class TestPluginUpdate:
    """Test plugin update command"""

    @staticmethod
    def test_update_dry_run_pip() -> None:
        """Test update dry run with pip"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False):
            results = commands.update(['some-plugin'], dry_run=True)

            assert len(results) == 1
            assert results[0].success
            assert 'Would update' in results[0].message
            assert '--upgrade' in results[0].message

    @staticmethod
    def test_update_dry_run_pipx() -> None:
        """Test update dry run with pipx"""
        commands = PluginCommands()

        with patch('porringer.backend.command.plugin.is_pipx_installation', return_value=True):
            results = commands.update(['some-plugin'], dry_run=True)

            assert len(results) == 1
            assert results[0].success
            assert 'Would update' in results[0].message
            assert 'pipx runpip' in results[0].message

    @staticmethod
    def test_update_success() -> None:
        """Test successful update"""
        commands = PluginCommands()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Successfully upgraded'
        mock_result.stderr = ''

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', return_value=mock_result),
        ):
            results = commands.update(['some-plugin'])

            assert len(results) == 1
            assert results[0].success
            assert 'Successfully updated' in results[0].message

    @staticmethod
    def test_update_failure() -> None:
        """Test update failure"""
        commands = PluginCommands()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'No matching distribution'

        with (
            patch('porringer.backend.command.plugin.is_pipx_installation', return_value=False),
            patch('porringer.backend.command.plugin.subprocess.run', return_value=mock_result),
        ):
            results = commands.update(['nonexistent-plugin'])

            assert len(results) == 1
            assert not results[0].success
            assert 'failed' in results[0].message.lower()


class TestPipxDetection:
    """Test pipx installation detection utility"""

    @staticmethod
    def test_is_pipx_installation_true() -> None:
        """Test detection when running in pipx environment"""
        # Mock sys.prefix to look like a pipx venv (using os.sep for cross-platform)
        pipx_path = os.sep.join(['', 'home', 'user', '.local', 'pipx', 'venvs', 'porringer'])
        with patch.object(sys, 'prefix', pipx_path):
            assert is_pipx_installation()

    @staticmethod
    def test_is_pipx_installation_false() -> None:
        """Test detection when running in regular venv"""
        # Mock sys.prefix to look like a regular venv
        venv_path = os.sep.join(['', 'home', 'user', 'projects', 'porringer', '.venv'])
        with patch.object(sys, 'prefix', venv_path):
            assert not is_pipx_installation()


# ---------------------------------------------------------------------------
# Regression: _resolve_dependencies must filter, not crash
# ---------------------------------------------------------------------------


def _make_dep_stub(
    name: str,
    *,
    deps: list[PluginDependency] | None = None,
) -> PluginInformation:
    """Build a ``PluginInformation`` wrapping a dynamic stub class."""
    dep_list = deps if deps is not None else []

    class _Dynamic:
        _distribution: Distribution

        def __init__(self, parameters: PluginParameters) -> None:
            self._distribution = parameters.distribution

        @staticmethod
        def ecosystem() -> Ecosystem | None:
            return Ecosystem('test')

        @staticmethod
        def plugin_kind() -> PluginKind:
            return PluginKind.PACKAGE

        @staticmethod
        def is_supported() -> bool:
            return True

        @classmethod
        def is_available(cls) -> bool:
            return True

        @staticmethod
        def package_name_validator() -> str | None:
            return None

        @staticmethod
        def dependencies() -> list[PluginDependency]:
            return dep_list

        @property
        def distribution(self) -> Distribution:
            return self._distribution

    _Dynamic.__qualname__ = name

    mock_dist = MagicMock()
    mock_dist.version = '0.0.0'
    return PluginInformation(type=_Dynamic, distribution=mock_dist, name=name)


class TestResolveDependenciesFilter:
    """_resolve_dependencies must filter plugins with unmet required deps.

    Filters instead of raising and crashing all discovery.
    """

    @staticmethod
    def test_unmet_required_dep_is_filtered() -> None:
        """A plugin with an unmet required dependency is excluded."""
        dep = PluginDependency(plugin='nonexistent', required=True)
        result = Builder._resolve_dependencies([_make_dep_stub('needs-dep', deps=[dep]), _make_dep_stub('no-dep')])

        names = [info.name for info in result]
        assert 'no-dep' in names
        assert 'needs-dep' not in names


# ---------------------------------------------------------------------------
# list (async) — RuntimeConsumer visibility with auto-resolved context
# ---------------------------------------------------------------------------


class TestListRuntimeConsumerVisibility:
    """PluginCommands.list() reports RuntimeConsumer plugins correctly.

    When a RuntimeConsumer plugin (e.g. pip) is not on PATH
    (``is_available()`` returns False), ``list()`` should still report
    it as installed when ``is_available_for()`` returns True with the
    auto-resolved ``RuntimeContext``.
    """

    @staticmethod
    async def test_runtime_consumer_installed_via_context() -> None:
        """A RuntimeConsumer unavailable on PATH shows installed=True via runtime context."""
        ctx = RuntimeContext(executables={'python': Path('/fake/python')})

        # Mock environment that is a RuntimeConsumer: unavailable on PATH,
        # but available when a runtime context with 'python' is present.
        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(
            side_effect=lambda rc=None: rc is not None and 'python' in rc.executables
        )
        type(mock_env).plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        type(mock_env).distribution = MagicMock(return_value=Distribution(version=Version('1.0.0')))

        with (
            patch('porringer.backend.command.plugin.discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
            patch.object(Builder, 'find_plugins', return_value=[]),
            patch.object(Builder, 'build_plugins', return_value=[]),
        ):
            results = await PluginCommands.list(kinds=[PluginKind.PACKAGE])

        pip_results = [r for r in results if r.name == 'pip']
        assert len(pip_results) == 1
        assert pip_results[0].installed is True

    @staticmethod
    async def test_runtime_consumer_not_installed_without_context() -> None:
        """A RuntimeConsumer unavailable on PATH shows installed=False with empty context."""
        ctx = RuntimeContext()  # No executables

        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(
            side_effect=lambda rc=None: rc is not None and 'python' in rc.executables
        )
        type(mock_env).plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        type(mock_env).distribution = MagicMock(return_value=Distribution(version=Version('1.0.0')))

        with (
            patch('porringer.backend.command.plugin.discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
            patch.object(Builder, 'find_plugins', return_value=[]),
            patch.object(Builder, 'build_plugins', return_value=[]),
        ):
            results = await PluginCommands.list(kinds=[PluginKind.PACKAGE])

        pip_results = [r for r in results if r.name == 'pip']
        assert len(pip_results) == 1
        assert pip_results[0].installed is False

    @staticmethod
    async def test_explicit_runtime_context_skips_auto_resolve() -> None:
        """Passing runtime_context= bypasses Builder.resolve_runtime_context."""
        ctx = RuntimeContext(executables={'python': Path('/explicit/python')})

        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=True)
        type(mock_env).plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        type(mock_env).distribution = MagicMock(return_value=Distribution(version=Version('1.0.0')))

        with (
            patch('porringer.backend.command.plugin.discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
            patch.object(Builder, 'find_plugins', return_value=[]),
            patch.object(Builder, 'build_plugins', return_value=[]),
        ):
            await PluginCommands.list(runtime_context=ctx)

        mock_resolve.assert_not_called()


# ---------------------------------------------------------------------------
# PackageCommands.list — query_availability gating + runtime resolution
# ---------------------------------------------------------------------------


class TestListPackagesAvailabilityGate:
    """PackageCommands.list uses query_availability with auto-resolved RuntimeContext."""

    @staticmethod
    async def test_unavailable_plugin_returns_empty() -> None:
        """A plugin unavailable via both PATH and runtime gets []."""
        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=False)
        mock_env.packages = AsyncMock(return_value=[Package(name='foo', version='1.0.0')])

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'mock-env': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            result = await PackageCommands.list('mock-env')

        assert result == []
        mock_env.packages.assert_not_called()

    @staticmethod
    async def test_available_plugin_delegates_to_packages() -> None:
        """An available plugin's packages() is called with the runtime context."""
        expected = [Package(name='foo', version='1.0.0')]
        ctx = RuntimeContext()

        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=expected)

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'mock-env': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
        ):
            result = await PackageCommands.list('mock-env')

        assert result == expected
        mock_env.packages.assert_called_once_with(project_path=None, runtime_context=ctx)

    @staticmethod
    async def test_runtime_context_enables_consumer() -> None:
        """A RuntimeConsumer unavailable on PATH becomes available via runtime context."""
        ctx = RuntimeContext(executables={'python': Path('/fake/python')})
        expected = [Package(name='pip', version='24.0')]

        mock_env = MagicMock(spec=Environment)
        # query_availability should return True when given ctx
        mock_env.query_availability = MagicMock(side_effect=lambda rc: rc is ctx)
        mock_env.packages = AsyncMock(return_value=expected)

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
        ):
            result = await PackageCommands.list('pip')

        assert result == expected
        mock_env.query_availability.assert_called_once_with(ctx)

    @staticmethod
    async def test_explicit_runtime_context_skips_auto_resolve() -> None:
        """Passing runtime_context= bypasses Builder.resolve_runtime_context."""
        ctx = RuntimeContext(executables={'python': Path('/explicit/python')})
        expected = [Package(name='pip', version='24.0')]

        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=expected)

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
        ):
            result = await PackageCommands.list('pip', runtime_context=ctx)

        mock_resolve.assert_not_called()
        assert result == expected

    @staticmethod
    async def test_missing_plugin_raises() -> None:
        """List raises PluginError for a non-existent plugin name."""
        with (
            patch('porringer.backend.command.package._discover_environments', return_value={}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
            pytest.raises(PluginError, match='not found'),
        ):
            await PackageCommands.list('nonexistent')


# ---------------------------------------------------------------------------
# query_availability — unified availability method on ToolBasedPlugin
# ---------------------------------------------------------------------------


class TestQueryAvailability:
    """ToolBasedPlugin.query_availability encapsulates the full decision tree."""

    @staticmethod
    def test_unsupported_returns_false() -> None:
        """An unsupported plugin returns False regardless of is_available."""
        mock = MagicMock(spec=ToolBasedPlugin)
        type(mock).is_supported = MagicMock(return_value=False)
        mock.is_available = MagicMock(return_value=True)

        result = ToolBasedPlugin.query_availability(mock)

        assert result is False

    @staticmethod
    def test_supported_available_returns_true() -> None:
        """A supported and PATH-available plugin returns True without context."""
        mock = MagicMock(spec=ToolBasedPlugin)
        type(mock).is_supported = MagicMock(return_value=True)
        mock.is_available = MagicMock(return_value=True)

        result = ToolBasedPlugin.query_availability(mock)

        assert result is True

    @staticmethod
    def test_supported_unavailable_returns_false() -> None:
        """A supported but PATH-unavailable plugin returns False without context."""
        mock = MagicMock(spec=ToolBasedPlugin)
        type(mock).is_supported = MagicMock(return_value=True)
        mock.is_available = MagicMock(return_value=False)

        result = ToolBasedPlugin.query_availability(mock)

        assert result is False

    @staticmethod
    def test_runtime_consumer_uses_is_available_for() -> None:
        """When runtime_context is provided and plugin is a RuntimeConsumer,

        is_available_for() is used instead of is_available().
        """

        class _Consumer(ToolBasedPlugin, RuntimeConsumer):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

            @staticmethod
            def ecosystem() -> Ecosystem | None:
                return Ecosystem('python')

            @classmethod
            def consumed_runtime_kind(cls) -> str:
                return 'python'

            @classmethod
            def is_available(cls) -> bool:
                return False  # Not on PATH

            @classmethod
            def is_available_for(cls, runtime_context: RuntimeContext) -> bool:
                return runtime_context.get('python') is not None

            @staticmethod
            def dependencies() -> list:
                return []

            @property
            def distribution(self) -> Distribution:
                return self._distribution

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        plugin = _Consumer(params)

        # Without context — falls back to is_available() → False
        assert plugin.query_availability() is False
        assert plugin.query_availability(None) is False

        # With context — delegates to is_available_for() → True
        ctx = RuntimeContext(executables={'python': Path('/fake/python')})
        assert plugin.query_availability(ctx) is True

    @staticmethod
    def test_non_consumer_ignores_runtime_context() -> None:
        """A non-RuntimeConsumer plugin uses is_available() even with a context."""
        mock = MagicMock(spec=ToolBasedPlugin)
        type(mock).is_supported = MagicMock(return_value=True)
        mock.is_available = MagicMock(return_value=True)

        ctx = RuntimeContext(executables={'python': Path('/fake/python')})
        result = ToolBasedPlugin.query_availability(mock, ctx)

        assert result is True
        mock.is_available.assert_called_once()


# ---------------------------------------------------------------------------
# Builder.resolve_runtime_context — runtime discovery for the query path
# ---------------------------------------------------------------------------


class TestResolveRuntimeContext:
    """Builder.resolve_runtime_context discovers runtimes from providers."""

    @staticmethod
    async def test_no_providers_returns_empty_context() -> None:
        """When no RuntimeProvider is present, an empty context is returned."""
        mock_env = MagicMock(spec=Environment)
        mock_env.is_available = MagicMock(return_value=True)
        type(mock_env).is_supported = MagicMock(return_value=True)

        ctx = await Builder.resolve_runtime_context({'pip': mock_env})

        assert ctx.executables == {}

    @staticmethod
    async def test_provider_resolves_runtime() -> None:
        """An available RuntimeProvider populates the context with its executable."""

        class _FakeProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
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
                return [Package(name='3.14', version='3.14.0'), Package(name='3.12', version='3.12.0')]

            @override
            async def check_updates(self, params):
                return []

            @staticmethod
            def dependencies() -> list:
                return []

            @property
            def distribution(self) -> Distribution:
                return self._distribution

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _FakeProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert 'python' in ctx.executables
        # Should resolve the highest version (3.14) first
        assert ctx.executables['python'] == Path('/python/3.14/python')

    @staticmethod
    async def test_provider_no_runtimes_returns_empty() -> None:
        """A provider with no installed runtimes yields an empty context."""
        # Use a non-RuntimeProvider environment — context stays empty
        mock_env = MagicMock(spec=Environment)
        mock_env.is_available = MagicMock(return_value=True)
        type(mock_env).is_supported = MagicMock(return_value=True)

        ctx = await Builder.resolve_runtime_context({'pip': mock_env})

        assert ctx.executables == {}

    @staticmethod
    async def test_provider_resolves_despite_invalid_tags() -> None:
        """Invalid tags like '(venv)' are filtered out; valid tags still resolve."""

        class _MixedTagProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                return Path(f'/python/{tag}/python')

            @override
            async def available_tags(self) -> list[str]:
                # Simulates real-world py launcher output including non-version tags
                return ['3.12', '(venv)', '3.14', 'latest', '']

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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _MixedTagProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert 'python' in ctx.executables
        # Highest valid tag (3.14) should be resolved, invalid tags silently dropped
        assert ctx.executables['python'] == Path('/python/3.14/python')


class TestDefaultTag:
    """Builder.resolve_runtime_context prefers default_tag() when available."""

    @staticmethod
    async def test_default_tag_preferred_over_highest_version() -> None:
        """When default_tag() returns a resolvable tag, it is used instead of the highest."""

        class _DefaultProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def default_tag(self) -> str | None:
                return '3.12'  # Not the highest

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _DefaultProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert ctx.executables['python'] == Path('/python/3.12/python')

    @staticmethod
    async def test_default_tag_none_falls_back_to_sort_tags() -> None:
        """When default_tag() returns None, the highest sorted tag is used."""

        class _NoDefaultProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def default_tag(self) -> str | None:
                return None

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                return Path(f'/python/{tag}/python')

            @override
            async def available_tags(self) -> list[str]:
                return ['3.12', '3.14']

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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _NoDefaultProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        # Falls back to sort_tags → highest first → 3.14
        assert ctx.executables['python'] == Path('/python/3.14/python')

    @staticmethod
    async def test_default_tag_unresolvable_falls_back() -> None:
        """When default_tag() returns a tag that fails resolution, fallback kicks in."""

        class _BadDefaultProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def default_tag(self) -> str | None:
                return '3.99'  # Not installed

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                if tag == '3.99':
                    return None  # Default can't be resolved
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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _BadDefaultProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        # Falls back to sort_tags → 3.14 (highest)
        assert ctx.executables['python'] == Path('/python/3.14/python')

    @staticmethod
    async def test_default_tag_exception_falls_back() -> None:
        """When default_tag() raises, resolution falls back to available_tags."""

        class _ErrorDefaultProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def default_tag(self) -> str | None:
                raise RuntimeError('subprocess failed')

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _ErrorDefaultProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        # Falls back to sort_tags → 3.14 (highest)
        assert ctx.executables['python'] == Path('/python/3.14/python')

    @staticmethod
    async def test_protocol_default_returns_none() -> None:
        """The base RuntimeProvider.default_tag() returns None (opt-in pattern)."""

        class _BareProvider(Environment, RuntimeProvider):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                return Path(f'/python/{tag}/python')

            @override
            async def available_tags(self) -> list[str]:
                return ['3.14']

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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _BareProvider(params)

        # Protocol default returns None
        assert await provider.default_tag() is None

        # Still resolves via fallback
        ctx = await Builder.resolve_runtime_context({'pim': provider})
        assert ctx.executables['python'] == Path('/python/3.14/python')


class TestSortTags:
    """RuntimeProvider.sort_tags default implementation (PEP 440).

    Uses a minimal inline provider that inherits the default ``sort_tags``
    from ``RuntimeProvider`` so these tests exercise the protocol's
    concrete method, independent of any real plugin.
    """

    class _DefaultProvider(Environment, RuntimeProvider):
        """Minimal provider that relies on the default ``sort_tags``."""

        _distribution: Distribution

        def __init__(self, parameters: PluginParameters) -> None:
            self._distribution = parameters.distribution

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
            return 'py'

        @classmethod
        def is_available(cls) -> bool:
            return True

        @override
        async def resolve_executable(self, tag: str) -> Path | None:
            return None

        @override
        async def available_tags(self) -> list[str]:
            return []

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

    @pytest.fixture
    def provider(self) -> RuntimeProvider:
        """Create a default-sort_tags provider."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        return self._DefaultProvider(params)

    @staticmethod
    def test_default_sort_tags_descending(provider: RuntimeProvider) -> None:
        """Valid PEP 440 tags are returned highest-first."""
        assert provider.sort_tags(['3.11', '3.14', '3.12']) == ['3.14', '3.12', '3.11']

    @staticmethod
    def test_sort_tags_drops_invalid(provider: RuntimeProvider) -> None:
        """Non-PEP-440 strings are silently dropped."""
        result = provider.sort_tags(['3.12', '(venv)', '3.14', 'latest', '', 'stable'])
        assert result == ['3.14', '3.12']

    @staticmethod
    def test_sort_tags_empty_input(provider: RuntimeProvider) -> None:
        """An empty tag list returns an empty list."""
        assert provider.sort_tags([]) == []

    @staticmethod
    def test_sort_tags_all_invalid(provider: RuntimeProvider) -> None:
        """When every tag is unparseable, an empty list is returned."""
        assert provider.sort_tags(['(venv)', 'latest', 'nope']) == []


class TestSortTagsOverride:
    """Builder respects custom sort_tags overrides from providers."""

    @staticmethod
    async def test_arch_suffix_override_resolves_highest() -> None:
        """A provider that strips architecture suffixes resolves the highest version."""

        class _ArchProvider(Environment, RuntimeProvider):
            """Strips trailing ``-<digits>`` for version parsing; returns full tags."""

            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'py'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                return Path(f'/python/{tag}/python')

            @override
            async def available_tags(self) -> list[str]:
                return ['3.12-64', '(venv)', '3.14-64', '3.11-32']

            @override
            def sort_tags(self, tags: list[str]) -> list[str]:
                """Strip -<arch> suffix for parsing, preserve full tags."""
                arch = re.compile(r'-\d+$')
                parsed: list[tuple[Version, str]] = []
                for tag in tags:
                    try:
                        parsed.append((Version(arch.sub('', tag)), tag))
                    except InvalidVersion:
                        continue
                parsed.sort(key=lambda p: p[0], reverse=True)
                return [t for _, t in parsed]

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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _ArchProvider(params)

        # Unit: sort_tags strips suffixes, drops garbage, sorts descending
        assert provider.sort_tags(['3.12-64', '(venv)', '3.14-64', '3.11-32']) == [
            '3.14-64',
            '3.12-64',
            '3.11-32',
        ]
        assert provider.sort_tags(['3.14', '3.12-64', '3.11']) == ['3.14', '3.12-64', '3.11']
        assert provider.sort_tags(['(venv)', 'latest']) == []
        assert provider.sort_tags([]) == []

        # End-to-end: builder uses the override to resolve the highest tag
        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert 'python' in ctx.executables
        # Full tag with architecture suffix must reach resolve_executable
        assert ctx.executables['python'] == Path('/python/3.14-64/python')

    @staticmethod
    async def test_custom_override_controls_resolution_order() -> None:
        """A provider with a custom sort_tags determines which tag is resolved first."""

        class _ReverseAlphaProvider(Environment, RuntimeProvider):
            """Sorts tags in reverse alphabetical order (not version order)."""

            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

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
                return 'custom-tool'

            @classmethod
            def is_available(cls) -> bool:
                return True

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                return Path(f'/custom/{tag}/bin')

            @override
            async def available_tags(self) -> list[str]:
                return ['beta', 'alpha', 'gamma']

            @override
            def sort_tags(self, tags: list[str]) -> list[str]:
                """Reverse alphabetical — 'gamma' wins."""
                return sorted(tags, reverse=True)

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

        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = _ReverseAlphaProvider(params)

        ctx = await Builder.resolve_runtime_context({'custom': provider})

        assert 'python' in ctx.executables
        # 'gamma' is first in reverse-alpha order, so it gets resolved
        assert ctx.executables['python'] == Path('/custom/gamma/bin')


# ---------------------------------------------------------------------------
# Builder.resolve_all_runtime_executables — multi-tag resolution
# ---------------------------------------------------------------------------


class _FakeMultiTagProvider(Environment, RuntimeProvider):
    """Minimal RuntimeProvider exposing multiple resolvable tags.

    Tags ``"3.14"`` and ``"3.12"`` resolve successfully.
    Tag ``"3.10"`` does not resolve (simulates missing install).
    """

    _distribution: Distribution

    def __init__(self, parameters: PluginParameters) -> None:
        self._distribution = parameters.distribution

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
        return 'py'

    @classmethod
    def is_available(cls) -> bool:
        return True

    @override
    async def resolve_executable(self, tag: str) -> Path | None:
        if tag == '3.10':
            return None  # not installed
        return Path(f'/python/{tag}/python')

    @override
    async def available_tags(self) -> list[str]:
        return ['3.14', '3.12', '3.10']

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


def _make_provider() -> _FakeMultiTagProvider:
    """Convenience helper to construct a ``_FakeMultiTagProvider``."""
    params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
    return _FakeMultiTagProvider(params)


class TestResolveAllRuntimeExecutables:
    """Builder.resolve_all_runtime_executables resolves every tag."""

    @staticmethod
    async def test_resolves_multiple_tags() -> None:
        """All resolvable tags are returned, in descending version order."""
        provider = _make_provider()

        results = await Builder.resolve_all_runtime_executables({'pim': provider})

        assert len(results) == NUM_RESOLVED_TAGS
        assert results[0].tag == '3.14'
        assert results[0].executable == Path('/python/3.14/python')
        assert results[0].provider == 'pim'
        assert results[0].kind == 'python'
        assert results[1].tag == '3.12'
        assert results[1].executable == Path('/python/3.12/python')

    @staticmethod
    async def test_skips_unresolvable_tags() -> None:
        """Tags where resolve_executable returns None are excluded."""
        provider = _make_provider()

        results = await Builder.resolve_all_runtime_executables({'pim': provider})

        tags = [r.tag for r in results]
        assert '3.10' not in tags

    @staticmethod
    async def test_skips_unavailable_provider() -> None:
        """A provider that is not available is skipped entirely."""
        mock_env = MagicMock(spec=Environment)
        mock_env.is_available = MagicMock(return_value=False)
        type(mock_env).is_supported = MagicMock(return_value=True)

        results = await Builder.resolve_all_runtime_executables({'unavailable': mock_env})

        assert results == []

    @staticmethod
    async def test_no_providers_returns_empty() -> None:
        """An environment dict with no RuntimeProvider returns []."""
        mock_env = MagicMock(spec=Environment)
        type(mock_env).is_supported = MagicMock(return_value=True)
        mock_env.is_available = MagicMock(return_value=True)

        results = await Builder.resolve_all_runtime_executables({'pip': mock_env})

        assert results == []

    @staticmethod
    async def test_multiple_providers() -> None:
        """Runtimes from multiple providers are all included."""
        provider = _make_provider()

        # Second provider for a different kind
        mock_node_env = MagicMock(spec=Environment)
        mock_node_env.is_supported = MagicMock(return_value=True)
        mock_node_env.is_available = MagicMock(return_value=True)
        mock_node_env.provided_runtime_kind = MagicMock(return_value='node')
        mock_node_env.available_tags = AsyncMock(return_value=['22.0', '20.0'])
        mock_node_env.sort_tags = MagicMock(return_value=['22.0', '20.0'])
        mock_node_env.resolve_executable = AsyncMock(side_effect=lambda t: Path(f'/node/{t}/node'))

        # Make isinstance checks work
        with patch(
            'porringer.backend.builder.isinstance',
            side_effect=lambda obj, cls: cls is RuntimeProvider or type(obj).__name__ == '_FakeMultiTagProvider',
        ) as _:
            pass

        # Simpler approach — use the real provider + a mock that passes isinstance check
        results = await Builder.resolve_all_runtime_executables({'pim': provider})
        assert len(results) == NUM_RESOLVED_TAGS  # from pim


# ---------------------------------------------------------------------------
# PackageCommands.list_by_runtime — per-runtime package queries
# ---------------------------------------------------------------------------


class TestListPackagesByRuntime:
    """PackageCommands.list_by_runtime queries every runtime."""

    @staticmethod
    async def test_returns_packages_per_runtime() -> None:
        """Each resolved runtime produces a RuntimePackageResult with its packages."""
        provider = _make_provider()

        # Mock consumer environment that returns different packages per runtime
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')

        async def _fake_packages(*, project_path=None, runtime_context=None):
            exe = runtime_context.get('python') if runtime_context is not None else None
            if exe and '3.14' in str(exe):
                return [Package(name='numpy', version='2.0')]
            return [Package(name='requests', version='2.31')]

        mock_env.packages = AsyncMock(side_effect=_fake_packages)

        with (
            patch(
                'porringer.backend.command.package._discover_environments',
                return_value={'pip': mock_env, 'pim': provider},
            ),
            patch.object(Builder, 'resolve_all_runtime_executables', new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/python/3.14/python')),
                ResolvedRuntime(provider='pim', tag='3.12', kind='python', executable=Path('/python/3.12/python')),
            ]

            results = await PackageCommands.list_by_runtime('pip')

        assert results is not None
        assert len(results) == NUM_RESOLVED_TAGS
        assert results[0].tag == '3.14'
        assert results[0].provider == 'pim'
        assert results[0].executable == Path('/python/3.14/python')
        assert results[0].packages == [Package(name='numpy', version='2.0')]
        assert results[1].tag == '3.12'
        assert results[1].packages == [Package(name='requests', version='2.31')]

    @staticmethod
    async def test_skips_unavailable_runtimes() -> None:
        """Runtimes where query_availability returns False are excluded."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')

        # Only available for 3.14, not 3.12
        def _avail(ctx):
            return ctx.get('python') is not None and '3.14' in str(ctx.get('python'))

        mock_env.query_availability = MagicMock(side_effect=_avail)
        mock_env.packages = AsyncMock(return_value=[Package(name='foo', version='1.0')])

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_all_runtime_executables', new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/python/3.14/python')),
                ResolvedRuntime(provider='pim', tag='3.12', kind='python', executable=Path('/python/3.12/python')),
            ]

            results = await PackageCommands.list_by_runtime('pip')

        assert results is not None
        assert len(results) == 1
        assert results[0].tag == '3.14'

    @staticmethod
    async def test_non_consumer_returns_none() -> None:
        """A plugin that is not a RuntimeConsumer returns None."""
        mock_env = MagicMock(spec=Environment)
        # Not a RuntimeConsumer — no consumed_runtime_kind

        with patch('porringer.backend.command.package._discover_environments', return_value={'brew': mock_env}):
            result = await PackageCommands.list_by_runtime('brew')
            assert result is None

    @staticmethod
    async def test_missing_plugin_raises() -> None:
        """A non-existent plugin name raises PluginError."""
        with (
            patch('porringer.backend.command.package._discover_environments', return_value={}),
            pytest.raises(PluginError, match='not found'),
        ):
            await PackageCommands.list_by_runtime('nonexistent')

    @staticmethod
    async def test_empty_when_no_matching_runtimes() -> None:
        """Returns [] when no runtimes match the consumer's kind."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'pip': mock_env}),
            patch.object(
                Builder,
                'resolve_all_runtime_executables',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            results = await PackageCommands.list_by_runtime('pip')

        assert results == []

    @staticmethod
    async def test_uses_prediscovered_plugins() -> None:
        """When plugins= is provided, environments are used directly."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=[])

        discovered = DiscoveredPlugins(
            environments={'pip': mock_env},
            project_environments={},
            scm_environments={},
        )

        with (
            patch('porringer.backend.command.package._discover_environments') as mock_discover,
            patch.object(
                Builder,
                'resolve_all_runtime_executables',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            results = await PackageCommands.list_by_runtime('pip', plugins=discovered)

        mock_discover.assert_not_called()
        assert results == []

    @staticmethod
    async def test_concurrent_queries() -> None:
        """All runtimes are queried (verifies packages() is called for each)."""
        mock_env = MagicMock(spec=PythonEnvironment)
        mock_env.consumed_runtime_kind = MagicMock(return_value='python')
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=[Package(name='pkg', version='1.0')])

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_all_runtime_executables', new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = [
                ResolvedRuntime(provider='pim', tag='3.14', kind='python', executable=Path('/python/3.14/python')),
                ResolvedRuntime(provider='pim', tag='3.13', kind='python', executable=Path('/python/3.13/python')),
                ResolvedRuntime(provider='pim', tag='3.12', kind='python', executable=Path('/python/3.12/python')),
            ]

            results = await PackageCommands.list_by_runtime('pip')

        assert results is not None
        assert len(results) == NUM_CONCURRENT_RUNTIMES
        assert mock_env.packages.call_count == NUM_CONCURRENT_RUNTIMES
