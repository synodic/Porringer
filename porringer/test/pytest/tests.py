"""Implementation of tests that should be overridden in plugins"""

import shutil
from abc import ABCMeta

from packaging.version import Version

import pytest
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Distribution, PluginParameters
from porringer.test.pytest.shared import (
    EnvironmentTests,
    PluginIntegrationTests,
    PluginUnitTests,
    ProjectEnvironmentTests,
    ScmEnvironmentTests,
)
from porringer.utility.utility import canonicalize_type


class EnvironmentIntegrationTests[T: Environment](PluginIntegrationTests[T], EnvironmentTests[T], metaclass=ABCMeta):
    """Base class for all environment integration tests that test plugin agnostic behavior"""

    @staticmethod
    def test_group_name(plugin_type: type[T]) -> None:
        """Verifies that the group name is the same as the plugin type

        Args:
            plugin_type: The type to register
        """
        assert canonicalize_type(plugin_type).group == 'environment'


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

    @staticmethod
    def test_group_name(plugin_type: type[T]) -> None:
        """Verifies that the group name matches the plugin type.

        Args:
            plugin_type: The type to register
        """
        assert canonicalize_type(plugin_type).group == 'scm'


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
