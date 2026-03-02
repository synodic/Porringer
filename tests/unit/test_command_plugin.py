"""Test the command 'plugin'"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from porringer.api import API
from porringer.backend.builder import Builder, PluginInformation
from porringer.backend.command.plugin import PluginCommands
from porringer.core.plugin_schema.environment import Environment
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
        with patch('porringer.backend.resolver.ToolBasedPlugin.tool_version', return_value=None):
            yield

    @staticmethod
    def test_plugin_list() -> None:
        """Test the plugin list"""
        config = LocalConfiguration()
        api = API(config)

        results = api.plugin.list()

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
# list_packages gates on is_available / is_supported
# ---------------------------------------------------------------------------


class TestListPackagesAvailabilityGate:
    """list_packages returns [] for unavailable/unsupported plugins without calling packages()."""

    @staticmethod
    async def test_unavailable_plugin_returns_empty() -> None:
        """An unavailable plugin gets [] without packages() being invoked."""
        mock_env = MagicMock(spec=Environment)
        type(mock_env).is_supported = MagicMock(return_value=True)
        mock_env.is_available = MagicMock(return_value=False)
        mock_env.packages = AsyncMock(return_value=[Package(name='foo', version='1.0.0')])

        with patch.object(PluginCommands, '_discover_environments', return_value={'mock-env': mock_env}):
            result = await PluginCommands.list_packages('mock-env')

        assert result == []
        mock_env.packages.assert_not_called()
