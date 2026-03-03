"""Implementation of tests that should be overridden in plugins"""

import shutil
from abc import ABCMeta

from packaging.version import Version

import pytest
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Distribution, PackageRef, PluginParameters
from porringer.test.pytest.shared import (
    EnvironmentTests,
    PluginIntegrationTests,
    PluginUnitTests,
    ProjectEnvironmentTests,
    RuntimeProviderTests,
    ScmEnvironmentTests,
)


class EnvironmentIntegrationTests[T: Environment](PluginIntegrationTests[T], EnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all environment integration tests that test plugin agnostic behavior"""


class EnvironmentUnitTests[T: Environment](PluginUnitTests[T], EnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all environment unit tests that test plugin agnostic behavior

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
    def test_uninstall_command_returns_list(plugin_type: type[T]) -> None:
        """uninstall_command() should return a non-empty list of strings."""
        params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
        instance = plugin_type(params)
        ref = PackageRef.model_validate('some-package')
        cmd = instance.uninstall_command(ref)
        assert isinstance(cmd, list)
        assert len(cmd) > 0
        assert all(isinstance(part, str) for part in cmd)


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
        kind = plugin_type.provided_runtime_kind()  # type: ignore[attr-defined]
        assert isinstance(kind, str)
        assert len(kind) > 0
