"""Tests for BackendResolver — plugin resolution without default_priority.

The resolver selects a plugin per (PluginKind, ecosystem) pair using:
1. Explicit user preferences (ecosystem → plugin name).
2. Alphabetical ordering among supported & available candidates.
"""

from packaging.version import Version

from porringer.backend.backend import BackendResolver
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters

# ---------------------------------------------------------------------------
# Helpers — lightweight stub plugins
# ---------------------------------------------------------------------------

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
_DEFAULT_ECOSYSTEM = Ecosystem('test')


class _StubPlugin:
    """Minimal plugin stub honouring the Plugin protocol."""

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


def _make(
    name: str,
    *,
    ecosystem: Ecosystem = _DEFAULT_ECOSYSTEM,
    kind: PluginKind = PluginKind.PACKAGE,
    supported: bool = True,
    available: bool = True,
) -> _StubPlugin:
    """Create a named stub plugin with configurable behaviour."""

    class _Dynamic(_StubPlugin):
        @staticmethod
        def ecosystem() -> Ecosystem | None:
            return ecosystem

        @staticmethod
        def plugin_kind() -> PluginKind:
            return kind

        @staticmethod
        def is_supported() -> bool:
            return supported

        @classmethod
        def is_available(cls) -> bool:
            return available

    _Dynamic.__qualname__ = name
    return _Dynamic(_PARAMS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolverAlphabeticalOrder:
    """When no preference is set the resolver picks alphabetically."""

    @staticmethod
    def test_single_candidate_selected() -> None:
        """Single candidate is selected."""
        plugins = {'alpha': _make('alpha')}
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'alpha'

    @staticmethod
    def test_alphabetically_first_wins() -> None:
        """Alphabetically first plugin wins."""
        plugins = {
            'charlie': _make('charlie'),
            'alpha': _make('alpha'),
            'bravo': _make('bravo'),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'alpha'

    @staticmethod
    def test_order_independent_of_insertion() -> None:
        """Resolution is independent of insertion order."""
        plugins_a = {'z': _make('z'), 'a': _make('a')}
        plugins_b = {'a': _make('a'), 'z': _make('z')}
        assert BackendResolver(plugins_a).resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'a'
        assert BackendResolver(plugins_b).resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'a'


class TestResolverPreferences:
    """Explicit preferences override alphabetical ordering."""

    @staticmethod
    def test_preference_overrides_alphabetical() -> None:
        """Explicit preference overrides alphabetical order."""
        plugins = {
            'alpha': _make('alpha'),
            'bravo': _make('bravo'),
        }
        resolver = BackendResolver(plugins, preferences={Ecosystem('test'): 'bravo'})
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'bravo'

    @staticmethod
    def test_unavailable_preference_falls_back() -> None:
        """Unavailable preferred plugin falls back to next candidate."""
        plugins = {
            'alpha': _make('alpha'),
            'bravo': _make('bravo', available=False),
        }
        resolver = BackendResolver(plugins, preferences={Ecosystem('test'): 'bravo'})
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'alpha'

    @staticmethod
    def test_unsupported_preference_falls_back() -> None:
        """Unsupported preferred plugin falls back to next candidate."""
        plugins = {
            'alpha': _make('alpha'),
            'bravo': _make('bravo', supported=False),
        }
        resolver = BackendResolver(plugins, preferences={Ecosystem('test'): 'bravo'})
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'alpha'

    @staticmethod
    def test_unknown_preference_falls_back() -> None:
        """Unknown preferred plugin falls back to next candidate."""
        plugins = {'alpha': _make('alpha')}
        resolver = BackendResolver(plugins, preferences={Ecosystem('test'): 'nonexistent'})
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'alpha'


class TestResolverPlatformSupport:
    """is_supported() excludes plugins before availability checks."""

    @staticmethod
    def test_unsupported_plugin_excluded() -> None:
        """Unsupported plugin is excluded from resolution."""
        plugins = {
            'only': _make('only', supported=False),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) is None

    @staticmethod
    def test_unsupported_skipped_in_favour_of_supported() -> None:
        """Unsupported plugin is skipped in favour of supported one."""
        plugins = {
            'alpha': _make('alpha', supported=False),
            'bravo': _make('bravo'),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'bravo'

    @staticmethod
    def test_all_unsupported_returns_none() -> None:
        """All unsupported plugins returns None."""
        plugins = {
            'alpha': _make('alpha', supported=False),
            'bravo': _make('bravo', supported=False),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) is None


class TestResolverAvailability:
    """is_available() filters out plugins whose tool is missing."""

    @staticmethod
    def test_unavailable_plugin_skipped() -> None:
        """Unavailable plugin is skipped."""
        plugins = {
            'alpha': _make('alpha', available=False),
            'bravo': _make('bravo'),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'bravo'

    @staticmethod
    def test_all_unavailable_returns_none() -> None:
        """All unavailable plugins returns None."""
        plugins = {
            'alpha': _make('alpha', available=False),
            'bravo': _make('bravo', available=False),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) is None


class TestResolverEcosystemIsolation:
    """Plugins from different ecosystems or kinds don't interfere."""

    @staticmethod
    def test_different_ecosystems_resolved_independently() -> None:
        """Different ecosystems are resolved independently."""
        plugins = {
            'a-py': _make('a-py', ecosystem=Ecosystem('python')),
            'b-node': _make('b-node', ecosystem=Ecosystem('node')),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('python')) == 'a-py'
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('node')) == 'b-node'

    @staticmethod
    def test_different_kinds_resolved_independently() -> None:
        """Different plugin kinds are resolved independently."""
        plugins = {
            'pkg': _make('pkg', kind=PluginKind.PACKAGE),
            'proj': _make('proj', kind=PluginKind.PROJECT),
        }
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('test')) == 'pkg'
        assert resolver.resolve(PluginKind.PROJECT, Ecosystem('test')) == 'proj'

    @staticmethod
    def test_unregistered_pair_returns_none() -> None:
        """Unregistered kind-ecosystem pair returns None."""
        plugins = {'alpha': _make('alpha', ecosystem=Ecosystem('python'))}
        resolver = BackendResolver(plugins)
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('node')) is None
