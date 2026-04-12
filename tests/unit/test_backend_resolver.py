"""Tests for BackendResolver — plugin resolution without default_priority.

The resolver selects a plugin per (PluginKind, ecosystem) pair using:
1. Explicit user preferences (ecosystem → plugin name).
2. Alphabetical ordering among supported & available candidates.
"""

from pathlib import Path
from typing import Self

from packaging.version import Version

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters
from porringer.core.transport import LocalTransport, Transport

# ---------------------------------------------------------------------------
# Helpers — lightweight stub plugins
# ---------------------------------------------------------------------------

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
_DEFAULT_ECOSYSTEM = Ecosystem('test')


class _StubPlugin:
    """Minimal plugin stub honouring the Plugin protocol."""

    _distribution: Distribution
    _transport: Transport

    def __init__(self, parameters: PluginParameters) -> None:
        self._distribution = parameters.distribution
        self._transport = LocalTransport()

    def with_transport(self, transport: Transport) -> Self:
        parameters = PluginParameters(distribution=self._distribution, transport=transport)
        return type(self)(parameters)

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


class TestResolverRegistration:
    """is_registered() and registered_names() distinguish registered-but-unavailable from unregistered."""

    @staticmethod
    def test_registered_and_available() -> None:
        """A registered and available plugin is reported as registered."""
        plugins = {'alpha': _make('alpha')}
        resolver = BackendResolver(plugins)
        assert resolver.is_registered(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is True
        assert resolver.registered_names(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) == ['alpha']
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) == 'alpha'

    @staticmethod
    def test_registered_but_unavailable() -> None:
        """All plugins registered but none available — is_registered is True, resolve is None."""
        plugins = {
            'alpha': _make('alpha', available=False),
            'bravo': _make('bravo', available=False),
        }
        resolver = BackendResolver(plugins)
        assert resolver.is_registered(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is True
        assert sorted(resolver.registered_names(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM)) == ['alpha', 'bravo']
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is None

    @staticmethod
    def test_not_registered_at_all() -> None:
        """No plugin registered for the pair — is_registered is False."""
        plugins = {'alpha': _make('alpha', ecosystem=Ecosystem('python'))}
        resolver = BackendResolver(plugins)
        assert resolver.is_registered(PluginKind.PACKAGE, Ecosystem('node')) is False
        assert resolver.registered_names(PluginKind.PACKAGE, Ecosystem('node')) == []
        assert resolver.resolve(PluginKind.PACKAGE, Ecosystem('node')) is None

    @staticmethod
    def test_empty_resolver() -> None:
        """An empty resolver has nothing registered."""
        resolver = BackendResolver({})
        assert resolver.is_registered(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is False


# ---------------------------------------------------------------------------
# Runtime-consumer stub — implements the RuntimeConsumer protocol
# ---------------------------------------------------------------------------


class _StubRuntimeConsumer(ToolBasedPlugin, RuntimeConsumer):
    """Plugin stub that also satisfies the ``RuntimeConsumer`` protocol.

    ``is_available()`` returns ``False`` (tool not on PATH) while
    ``is_available_for()`` can be configured to return ``True``
    (module present in the target interpreter).
    """

    _available_for: bool = True

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        return 'python'

    @classmethod
    def is_available(cls) -> bool:
        # Simulates "pip not on PATH"
        return False

    @classmethod
    def is_available_for(cls, runtime_context: RuntimeContext) -> bool:
        return cls._available_for

    @staticmethod
    def ecosystem() -> Ecosystem | None:
        return Ecosystem('test')

    @staticmethod
    def plugin_kind() -> PluginKind:
        return PluginKind.PACKAGE

    @staticmethod
    def dependencies() -> list:
        return []


def _make_consumer(
    name: str,
    *,
    available_for: bool = True,
    ecosystem: Ecosystem = _DEFAULT_ECOSYSTEM,
) -> _StubRuntimeConsumer:
    """Create a named ``RuntimeConsumer`` stub plugin."""

    class _DynConsumer(_StubRuntimeConsumer):
        _available_for = available_for

        @staticmethod
        def ecosystem() -> Ecosystem | None:
            return ecosystem

    _DynConsumer.__qualname__ = name
    return _DynConsumer(_PARAMS)


# ---------------------------------------------------------------------------
# Runtime-context tests
# ---------------------------------------------------------------------------


class TestResolverRuntimeContext:
    """When ``runtime_context`` is supplied, RuntimeConsumer plugins are probed

    via ``is_available_for()`` instead of ``is_available()``.
    """

    @staticmethod
    def test_consumer_discovered_via_runtime_context() -> None:
        """A plugin that fails is_available() succeeds via is_available_for()."""
        ctx = RuntimeContext(executables={'python': Path('/usr/bin/python3')})
        plugins = {'pip': _make_consumer('pip', available_for=True)}
        resolver = BackendResolver(plugins, runtime_context=ctx)
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) == 'pip'

    @staticmethod
    def test_consumer_rejected_by_runtime_context() -> None:
        """A plugin that fails both is_available() and is_available_for() is excluded."""
        ctx = RuntimeContext(executables={'python': Path('/usr/bin/python3')})
        plugins = {'pip': _make_consumer('pip', available_for=False)}
        resolver = BackendResolver(plugins, runtime_context=ctx)
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is None

    @staticmethod
    def test_without_runtime_context_uses_is_available() -> None:
        """Without a runtime_context, a consumer failing is_available() is excluded."""
        plugins = {'pip': _make_consumer('pip', available_for=True)}
        resolver = BackendResolver(plugins)  # no runtime_context
        # is_available() returns False on _StubRuntimeConsumer, so it should be excluded
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) is None

    @staticmethod
    def test_mixed_consumer_and_plain_plugins() -> None:
        """RuntimeConsumer and plain plugins coexist; only the consumer uses runtime probing."""
        ctx = RuntimeContext(executables={'python': Path('/usr/bin/python3')})
        plugins = {
            'pip': _make_consumer('pip', available_for=True),
            'uv': _make('uv', available=True),
        }
        resolver = BackendResolver(plugins, runtime_context=ctx)
        # Both available — alphabetically 'pip' < 'uv'
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) == 'pip'

    @staticmethod
    def test_alphabetical_among_runtime_consumers() -> None:
        """Among multiple available consumers, alphabetical ordering still applies."""
        ctx = RuntimeContext(executables={'python': Path('/usr/bin/python3')})
        plugins = {
            'zeta': _make_consumer('zeta', available_for=True),
            'alpha': _make_consumer('alpha', available_for=True),
        }
        resolver = BackendResolver(plugins, runtime_context=ctx)
        assert resolver.resolve(PluginKind.PACKAGE, _DEFAULT_ECOSYSTEM) == 'alpha'
