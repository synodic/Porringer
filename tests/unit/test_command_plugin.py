"""Test the command 'plugin'"""

import os
import sys
from pathlib import Path
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.version import Version

from porringer.api import API
from porringer.backend.builder import Builder, PluginInformation
from porringer.backend.command.plugin import PluginCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext, RuntimeProvider
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Distribution, Ecosystem, Package, PluginDependency, PluginKind, PluginParameters
from porringer.schema import LocalConfiguration
from porringer.utility.exception import PluginError
from porringer.utility.utility import is_pipx_installation

# Test constants
NUM_PLUGINS_MULTIPLE = 3
NUM_PLUGINS_PARTIAL = 2


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
            patch.object(PluginCommands, '_discover_environments', return_value={'pip': mock_env}),
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
            patch.object(PluginCommands, '_discover_environments', return_value={'pip': mock_env}),
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
            patch.object(PluginCommands, '_discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
            patch.object(Builder, 'find_plugins', return_value=[]),
            patch.object(Builder, 'build_plugins', return_value=[]),
        ):
            await PluginCommands.list(runtime_context=ctx)

        mock_resolve.assert_not_called()


# ---------------------------------------------------------------------------
# list_packages — query_availability gating + runtime resolution
# ---------------------------------------------------------------------------


class TestListPackagesAvailabilityGate:
    """list_packages uses query_availability with auto-resolved RuntimeContext."""

    @staticmethod
    async def test_unavailable_plugin_returns_empty() -> None:
        """A plugin unavailable via both PATH and runtime gets []."""
        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=False)
        mock_env.packages = AsyncMock(return_value=[Package(name='foo', version='1.0.0')])

        with (
            patch.object(PluginCommands, '_discover_environments', return_value={'mock-env': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            result = await PluginCommands.list_packages('mock-env')

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
            patch.object(PluginCommands, '_discover_environments', return_value={'mock-env': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
        ):
            result = await PluginCommands.list_packages('mock-env')

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
            patch.object(PluginCommands, '_discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=ctx),
        ):
            result = await PluginCommands.list_packages('pip')

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
            patch.object(PluginCommands, '_discover_environments', return_value={'pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock) as mock_resolve,
        ):
            result = await PluginCommands.list_packages('pip', runtime_context=ctx)

        mock_resolve.assert_not_called()
        assert result == expected

    @staticmethod
    async def test_missing_plugin_raises() -> None:
        """list_packages raises PluginError for a non-existent plugin name."""
        with (
            patch.object(PluginCommands, '_discover_environments', return_value={}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
            pytest.raises(PluginError, match='not found'),
        ):
            await PluginCommands.list_packages('nonexistent')


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
