"""Helpers for test plugin tags."""

"""Tests for runtime tag resolution and ordering."""

import re
from pathlib import Path
from typing import override

import pytest
from packaging.version import InvalidVersion, Version

from porringer.backend.builder import Builder
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters

# Test constants
NUM_PLUGINS_MULTIPLE = 3
NUM_PLUGINS_PARTIAL = 2
NUM_RESOLVED_TAGS = 2
NUM_CONCURRENT_RUNTIMES = 3


class TestDefaultTag:
    """Builder.resolve_runtime_context prefers default_tag() when available."""

    @staticmethod
    async def test_default_executable_preferred_over_default_tag() -> None:
        """Providers can resolve their default executable without a second tag lookup."""

        class _DefaultExecutableProvider(Environment, RuntimeProvider):
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

            @staticmethod
            async def default_executable() -> Path | None:
                """Resolve the default executable directly."""
                return Path('/python/default/python')

            @override
            async def default_tag(self) -> str | None:
                raise AssertionError('default_tag should not be called')

            @override
            async def resolve_executable(self, tag: str) -> Path | None:
                raise AssertionError(f'resolve_executable should not be called for {tag}')

            @override
            async def available_tags(self) -> list[str]:
                raise AssertionError('available_tags should not be called')

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
        provider = _DefaultExecutableProvider(params)

        ctx = await Builder.resolve_runtime_context({'pim': provider})

        assert ctx.executables['python'] == Path('/python/default/python')

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
    @pytest.mark.parametrize(
        ('tags', 'expected'),
        [
            pytest.param(['3.11', '3.14', '3.12'], ['3.14', '3.12', '3.11'], id='descending'),
            pytest.param(['3.12', '(venv)', '3.14', 'latest', '', 'stable'], ['3.14', '3.12'], id='drops-invalid'),
            pytest.param([], [], id='empty'),
            pytest.param(['(venv)', 'latest', 'nope'], [], id='all-invalid'),
        ],
    )
    def test_default_sort_tags(provider: RuntimeProvider, tags: list[str], expected: list[str]) -> None:
        """Valid PEP 440 tags sort highest-first; unparseable tags are dropped."""
        assert provider.sort_tags(tags) == expected


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
