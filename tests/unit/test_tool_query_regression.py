"""Regression tests for the tool-query pipeline.

Guards against two regressions:
  1. ``_resolve_dependencies`` raising on unmet required deps instead of
     filtering — which crashed all environment discovery.
  2. ``list_packages`` hard-gating on ``is_available()`` — which blocked
     plugins whose ``packages()`` method has internal fallbacks.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.version import Version

from porringer.backend.builder import Builder, PluginInformation
from porringer.backend.command.plugin import PluginCommands
from porringer.backend.resolver import build_plugin_info
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PluginDependency,
    PluginKind,
    PluginParameters,
)
from porringer.utility.exception import PluginError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


class _StubPlugin:
    """Minimal plugin stub conforming to the Plugin protocol."""

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
    def dependencies() -> list:
        return []

    @property
    def distribution(self) -> Distribution:
        return self._distribution


def _make_info(
    name: str,
    *,
    deps: list[PluginDependency] | None = None,
) -> PluginInformation:
    """Build a ``PluginInformation`` wrapping a dynamic stub class."""
    dep_list = deps if deps is not None else []

    class _Dynamic(_StubPlugin):
        @staticmethod
        def dependencies() -> list[PluginDependency]:
            return dep_list

    _Dynamic.__qualname__ = name

    mock_dist = MagicMock()
    mock_dist.version = '0.0.0'
    return PluginInformation(type=_Dynamic, distribution=mock_dist, name=name)


# ---------------------------------------------------------------------------
# _resolve_dependencies — filter, don't crash
# ---------------------------------------------------------------------------


class TestResolveDependenciesFilter:
    """Verify that _resolve_dependencies filters plugins with unmet required dependencies.

    Filters instead of raising and crashing all discovery.
    """

    @staticmethod
    def test_unmet_required_dep_is_filtered() -> None:
        """A plugin with an unmet required dependency is excluded."""
        needed_dep = PluginDependency(plugin='nonexistent-plugin', required=True)
        stub_with_dep = _make_info('needs-dep', deps=[needed_dep])
        stub_no_dep = _make_info('no-dep')

        result = Builder._resolve_dependencies([stub_with_dep, stub_no_dep])

        assert len(result) == 1
        assert result[0].name == 'no-dep'

    @staticmethod
    def test_unmet_required_dep_does_not_crash() -> None:
        """No exception is raised when a required dependency is missing."""
        needed_dep = PluginDependency(plugin='nonexistent-plugin', required=True)
        stub = _make_info('needs-dep', deps=[needed_dep])

        # Should return an empty list, not raise
        result = Builder._resolve_dependencies([stub])
        assert result == []

    @staticmethod
    def test_valid_plugin_survives_after_filtered_one() -> None:
        """A valid plugin that comes after a filtered one is still returned."""
        needed_dep = PluginDependency(plugin='nonexistent-plugin', required=True)
        stub_bad = _make_info('bad-plugin', deps=[needed_dep])
        stub_good = _make_info('good-plugin')

        result = Builder._resolve_dependencies([stub_bad, stub_good])

        names = [info.name for info in result]
        assert 'good-plugin' in names
        assert 'bad-plugin' not in names

    @staticmethod
    def test_satisfied_deps_are_kept() -> None:
        """When two plugins satisfy each other's deps, both are returned."""
        dep_on_b = PluginDependency(plugin='plugin-b', required=True)
        dep_on_a = PluginDependency(plugin='plugin-a', required=True)
        stub_a = _make_info('plugin-a', deps=[dep_on_b])
        stub_b = _make_info('plugin-b', deps=[dep_on_a])

        result = Builder._resolve_dependencies([stub_a, stub_b])

        names = {info.name for info in result}
        assert names == {'plugin-a', 'plugin-b'}

    @staticmethod
    def test_optional_unmet_dep_is_kept() -> None:
        """A plugin with an unmet optional dependency is still included."""
        optional_dep = PluginDependency(plugin='nonexistent-plugin', required=False)
        stub = _make_info('has-optional', deps=[optional_dep])

        result = Builder._resolve_dependencies([stub])

        assert len(result) == 1
        assert result[0].name == 'has-optional'

    @staticmethod
    def test_platform_inapplicable_dep_is_ignored() -> None:
        """A required dep scoped to a different platform doesn't filter."""
        # Use a platform that will never match the test runner
        impossible_platform = 'not-a-real-platform'
        dep = PluginDependency(plugin='nonexistent-plugin', required=True, platforms=[impossible_platform])
        stub = _make_info('cross-platform', deps=[dep])

        result = Builder._resolve_dependencies([stub])

        assert len(result) == 1
        assert result[0].name == 'cross-platform'


# ---------------------------------------------------------------------------
# list_packages — no is_available gate
# ---------------------------------------------------------------------------


class TestListPackagesNoAvailabilityGate:
    """Verify that list_packages delegates to packages() even when is_available() returns False.

    Lets the plugin's own fallbacks work.
    """

    @staticmethod
    async def test_unavailable_plugin_delegates_to_packages() -> None:
        """list_packages returns results from packages() even when is_available() is False."""
        mock_env = MagicMock(spec=Environment)
        mock_env.is_available = classmethod(lambda cls: False)

        expected = [Package(name='foo', version='1.0.0')]
        mock_env.packages = AsyncMock(return_value=expected)

        environments = {'mock-env': mock_env}

        with patch.object(PluginCommands, '_discover_environments', return_value=environments):
            result = await PluginCommands.list_packages('mock-env')

        assert result == expected
        mock_env.packages.assert_called_once()

    @staticmethod
    async def test_unavailable_plugin_returns_empty_list() -> None:
        """list_packages returns [] without raising when packages() returns [] for an unavailable plugin."""
        mock_env = MagicMock(spec=Environment)
        mock_env.is_available = classmethod(lambda cls: False)
        mock_env.packages = AsyncMock(return_value=[])

        environments = {'mock-env': mock_env}

        with patch.object(PluginCommands, '_discover_environments', return_value=environments):
            result = await PluginCommands.list_packages('mock-env')

        assert result == []

    @staticmethod
    async def test_missing_plugin_still_raises() -> None:
        """list_packages still raises PluginError for a plugin name that doesn't exist at all."""
        environments: dict[str, Environment] = {}

        with (
            patch.object(PluginCommands, '_discover_environments', return_value=environments),
            pytest.raises(PluginError, match='not found'),
        ):
            await PluginCommands.list_packages('nonexistent')


# ---------------------------------------------------------------------------
# build_plugin_info — unavailable tool-based plugin
# ---------------------------------------------------------------------------


class TestBuildPluginInfoUnavailableToolPlugin:
    """Verify that build_plugin_info populates installed=False and tool_version=None.

    Targets unavailable ToolBasedPlugin subclasses.
    """

    @staticmethod
    def test_unavailable_tool_plugin_info() -> None:
        """An unavailable ToolBasedPlugin yields installed=False, tool_version=None."""

        class _Unavailable(ToolBasedPlugin):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

            @classmethod
            def tool_name(cls) -> str:
                return 'nonexistent_tool_xyz_test'

            @staticmethod
            def ecosystem() -> Ecosystem | None:
                return Ecosystem('test')

            @staticmethod
            def plugin_kind() -> PluginKind:
                return PluginKind.TOOL

            @staticmethod
            def dependencies() -> list:
                return []

            @property
            def distribution(self) -> Distribution:
                return self._distribution

        plugin = _Unavailable(_PARAMS)
        results = build_plugin_info({'unavailable-tool': plugin})

        assert len(results) == 1
        info = results[0]
        assert info.name == 'unavailable-tool'
        assert info.installed is False
        assert info.tool_version is None

    @staticmethod
    def test_tool_version_called_only_when_installed() -> None:
        """tool_version() is not called for unavailable plugins."""

        class _Available(ToolBasedPlugin):
            _distribution: Distribution

            def __init__(self, parameters: PluginParameters) -> None:
                self._distribution = parameters.distribution

            @classmethod
            def is_available(cls) -> bool:
                return False

            @classmethod
            def tool_name(cls) -> str:
                return 'fake-tool'

            @staticmethod
            def ecosystem() -> Ecosystem | None:
                return Ecosystem('test')

            @staticmethod
            def plugin_kind() -> PluginKind:
                return PluginKind.PACKAGE

            @staticmethod
            def dependencies() -> list:
                return []

            @property
            def distribution(self) -> Distribution:
                return self._distribution

        plugin = _Available(_PARAMS)

        with patch.object(type(plugin), 'tool_version') as mock_tv:
            build_plugin_info({'fake-tool': plugin})
            mock_tv.assert_not_called()
