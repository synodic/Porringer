"""Tests covering the tests behavior."""

"""Implementation of tests that should be overridden in plugins."""

import shutil
from abc import ABCMeta, abstractmethod
from collections.abc import Generator
from typing import NamedTuple
from unittest.mock import AsyncMock

from packaging.version import Version

import pytest
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Distribution, PackageRef, PluginParameters
from porringer.test.pytest.shared import (
    EnvironmentTests,
    PluginIntegrationTests,
    PluginUnitTests,
    ProjectEnvironmentTests,
    RuntimeProviderTests,
    ScmEnvironmentTests,
)

# Package names that probe argv-injection and token-splitting bugs.  A
# correct ``list``-based command builder confines each to exactly one
# argv token, so neither the token count nor the structure may change
# with the name's content.
_ADVERSARIAL_PACKAGE_NAMES = (
    'pkg; rm -rf /',
    'pkg && evil',
    'pkg $(whoami)',
    '--config=evil',
    'a b c',
    'pkg\nrm',
)

_BENIGN_PACKAGE_NAME = 'benignpackage'


def _environment_instance[T: Environment](plugin_type: type[T]) -> T:
    """Construct a plugin instance with placeholder distribution metadata."""
    return plugin_type(PluginParameters(distribution=Distribution(version=Version('0.0.0'))))


def _command_for_verb(
    instance: Environment,
    verb: str,
    package: PackageRef,
    *,
    include_prereleases: bool = False,
) -> list[str]:
    """Dispatch to the install / upgrade / uninstall builder for *verb*."""
    if verb == 'install':
        return instance.install_command(package, include_prereleases=include_prereleases)
    if verb == 'upgrade':
        return instance.upgrade_command(package, include_prereleases=include_prereleases)
    return instance.uninstall_command(package)


def _is_subsequence(small: list[str], large: list[str]) -> bool:
    """Return whether *small* occurs in *large* in order (gaps allowed)."""
    iterator = iter(large)
    return all(token in iterator for token in small)


class EnvironmentIntegrationTests[T: Environment](PluginIntegrationTests[T], EnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all environment integration tests that test plugin agnostic behavior."""


class EnvironmentUnitTests[T: Environment](PluginUnitTests[T], EnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all environment unit tests that test plugin agnostic behavior.

    Custom implementations of the environment class should inherit from this class for its tests.
    """

    @staticmethod
    def test_is_available_returns_true(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return True when the tool is found on PATH."""
        tool = plugin_type.tool_name()
        if tool is None:
            # Plugins without a CLI tool are always available
            assert plugin_type.is_available() is True
            return
        monkeypatch.setattr(shutil, 'which', lambda cmd: f'/usr/bin/{tool}' if cmd == tool else None)
        assert plugin_type.is_available() is True

    @staticmethod
    def test_is_available_returns_false(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return False when the tool is not on PATH."""
        tool = plugin_type.tool_name()
        if tool is None:
            # Plugins without a CLI tool are always available
            assert plugin_type.is_available() is True
            return
        monkeypatch.setattr(shutil, 'which', lambda cmd: None)
        assert plugin_type.is_available() is False

    @staticmethod
    @pytest.mark.parametrize('verb', ['install', 'upgrade', 'uninstall'])
    def test_command_confines_package_to_single_token(plugin_type: type[T], verb: str) -> None:
        """Each builder keeps the package within exactly one argv token.

        Builders construct ``list`` argv, so a hostile package name must
        never split across tokens nor change the argv structure.  The
        name stays contained in a single token (a plugin may legitimately
        decorate it, e.g. ``npm:name`` or ``name@latest``), and the
        token count is invariant to the name's content.
        """
        instance = _environment_instance(plugin_type)
        baseline = _command_for_verb(instance, verb, PackageRef(name=_BENIGN_PACKAGE_NAME))
        assert isinstance(baseline, list)
        assert baseline
        assert all(isinstance(part, str) for part in baseline)
        for name in _ADVERSARIAL_PACKAGE_NAMES:
            cmd = _command_for_verb(instance, verb, PackageRef(name=name))
            containing = [token for token in cmd if name in token]
            assert len(containing) == 1
            assert len(cmd) == len(baseline)

    @staticmethod
    @pytest.mark.parametrize('verb', ['install', 'upgrade'])
    def test_prerelease_flag_is_additive(plugin_type: type[T], verb: str) -> None:
        """Enabling prereleases only *adds* argv tokens, never removes them.

        The command without prereleases must remain an ordered
        subsequence of the command with them, so the option can only
        extend — never rewrite — the base invocation.
        """
        instance = _environment_instance(plugin_type)
        ref = PackageRef(name=_BENIGN_PACKAGE_NAME)
        without = _command_for_verb(instance, verb, ref, include_prereleases=False)
        with_pre = _command_for_verb(instance, verb, ref, include_prereleases=True)
        assert _is_subsequence(without, with_pre)

    @staticmethod
    @pytest.mark.parametrize('verb', ['install', 'upgrade', 'uninstall'])
    def test_command_is_deterministic(plugin_type: type[T], verb: str) -> None:
        """Building the same command twice yields identical argv."""
        instance = _environment_instance(plugin_type)
        ref = PackageRef(name=_BENIGN_PACKAGE_NAME, constraint='>=1.0')
        assert _command_for_verb(instance, verb, ref) == _command_for_verb(instance, verb, ref)


class ProjectEnvironmentUnitTests[T: ProjectEnvironment](
    PluginUnitTests[T], ProjectEnvironmentTests[T], metaclass=ABCMeta
):
    """Base class for all project-environment unit tests.

    Custom implementations of `ProjectEnvironment` should inherit
    from this class for their tests.
    """

    @staticmethod
    def test_is_available_returns_true(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return True when the tool is found on PATH."""
        tool = plugin_type.tool_name()
        monkeypatch.setattr(shutil, 'which', lambda cmd: f'/usr/bin/{tool}' if cmd == tool else None)
        assert plugin_type.is_available() is True

    @staticmethod
    def test_is_available_returns_false(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return False when the tool is not on PATH."""
        monkeypatch.setattr(shutil, 'which', lambda cmd: None)
        assert plugin_type.is_available() is False

    @staticmethod
    def test_sync_command_returns_list(plugin_type: type[T]) -> None:
        """sync_command() should return a non-empty list of strings."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        instance = plugin_type(params)
        cmd = instance.sync_command()
        assert isinstance(cmd, list)
        assert len(cmd) > 0
        assert all(isinstance(part, str) for part in cmd)

    @staticmethod
    def test_ecosystem_returns_string(plugin_type: type[T]) -> None:
        """ecosystem() should return a non-empty string identifier."""
        eco = plugin_type.ecosystem()
        assert isinstance(eco, str)
        assert len(eco) > 0


class ScmEnvironmentIntegrationTests[T: ScmEnvironment](
    PluginIntegrationTests[T], ScmEnvironmentTests[T], metaclass=ABCMeta
):
    """Base class for all SCM-environment integration tests."""


class ScmEnvironmentUnitTests[T: ScmEnvironment](PluginUnitTests[T], ScmEnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all SCM-environment unit tests.

    Custom implementations of `ScmEnvironment` should inherit
    from this class for their tests.
    """

    @staticmethod
    def test_is_available_returns_true(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return True when the tool is found on PATH."""
        tool = plugin_type.tool_name()
        monkeypatch.setattr(shutil, 'which', lambda cmd: f'/usr/bin/{tool}' if cmd == tool else None)
        assert plugin_type.is_available() is True

    @staticmethod
    def test_is_available_returns_false(monkeypatch: pytest.MonkeyPatch, plugin_type: type[T]) -> None:
        """is_available() should return False when the tool is not on PATH."""
        monkeypatch.setattr(shutil, 'which', lambda cmd: None)
        assert plugin_type.is_available() is False

    @staticmethod
    def test_ecosystem_returns_string(plugin_type: type[T]) -> None:
        """ecosystem() should return a non-empty string identifier."""
        eco = plugin_type.ecosystem()
        assert isinstance(eco, str)
        assert len(eco) > 0


class RuntimeProviderUnitTests[T: Environment](EnvironmentUnitTests[T], RuntimeProviderTests[T], metaclass=ABCMeta):
    """Unit tests for ``Environment`` plugins that also implement ``RuntimeProvider``.

    These tests verify behavioural contracts that every ``RuntimeProvider``
    implementation must satisfy, regardless of its versioning scheme.

    Custom implementations should inherit from this class and provide a
    ``fixture_plugin_type`` fixture returning the concrete plugin type.
    """

    @staticmethod
    def test_sort_tags_empty_returns_empty(plugin_type: type[T]) -> None:
        """sort_tags([]) must return an empty list."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = plugin_type(params)
        assert isinstance(provider, RuntimeProvider)
        assert provider.sort_tags([]) == []

    @staticmethod
    def test_sort_tags_result_is_subset_of_input(plugin_type: type[T]) -> None:
        """Every tag in the result must appear in the original input."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = plugin_type(params)
        assert isinstance(provider, RuntimeProvider)
        tags = ['3.14', '3.12', '(venv)', 'nope', '', '3.11']
        result = provider.sort_tags(tags)
        assert set(result) <= set(tags)

    @staticmethod
    def test_sort_tags_does_not_crash_on_garbage(plugin_type: type[T]) -> None:
        """sort_tags must never raise, even on completely unparseable input."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        provider = plugin_type(params)
        assert isinstance(provider, RuntimeProvider)
        result = provider.sort_tags(['(venv)', 'latest', '', '!!!', 'not-a-version'])
        assert isinstance(result, list)

    @staticmethod
    def test_provided_runtime_kind_is_nonempty(plugin_type: type[T]) -> None:
        """provided_runtime_kind() must return a non-empty string."""
        assert hasattr(plugin_type, 'provided_runtime_kind')
        kind = plugin_type.provided_runtime_kind()
        assert isinstance(kind, str)
        assert len(kind) > 0


class InstallContext(NamedTuple):
    """Context returned by the ``install_context`` fixture."""

    plugin: Environment
    params: PackageParameters
    mock_run: AsyncMock
    scenario: str


class AuxiliaryToolTests[T: ToolBasedPlugin](metaclass=ABCMeta):
    """Mixin that tests auxiliary-tool interactions declared via ``_auxiliary_tools``.

    Concrete test classes must provide:

    * ``fixture_install_context`` — a fixture yielding an
      :class:`InstallContext` whose ``scenario`` field indicates which
      auxiliary-tool configuration is active.

    Expected scenarios (the fixture should be parametrized over these):

    * ``"tools_absent"`` — ``shutil.which`` returns ``None`` for
      auxiliary tools; ``run_command`` is not expected to be called
      for them.
    * ``"tools_present"`` — ``shutil.which`` returns a fake path;
      ``run_command`` mock returns success.
    * ``"tools_fail_exception"`` — ``shutil.which`` returns a fake
      path; ``run_command`` raises ``OSError`` for auxiliary tools.
    * ``"tools_fail_nonzero"`` — ``shutil.which`` returns a fake path;
      ``run_command`` returns non-zero for auxiliary tools.

    The mixin is intentionally separate from ``EnvironmentUnitTests`` so
    that plugins without auxiliary tools don't inherit meaningless tests.
    """

    @abstractmethod
    @pytest.fixture(name='install_context')
    def fixture_install_context(self) -> Generator[InstallContext]:
        """Yield an ``InstallContext`` with install internals mocked."""
        raise NotImplementedError('Override this fixture')

    @staticmethod
    async def test_install_succeeds(install_context: InstallContext) -> None:
        """install() succeeds regardless of auxiliary tool availability."""
        plugin, params, _mock_run, _scenario = install_context
        result = await plugin.install(params)
        # Install should never fail due to auxiliary tool issues
        assert result is not None

    @staticmethod
    async def test_auxiliary_tools_called_only_when_present(
        install_context: InstallContext,
    ) -> None:
        """Auxiliary tools are invoked when present, skipped when absent."""
        plugin, params, mock_run, scenario = install_context
        aux = type(plugin).auxiliary_tools()
        if not aux:
            pytest.skip('No auxiliary tools declared')

        await plugin.install(params)

        called_args = [str(c) for c in mock_run.call_args_list]
        if scenario == 'tools_absent':
            assert not any(tool in arg for arg in called_args for tool in aux)
        else:
            assert any(tool in arg for arg in called_args for tool in aux)
