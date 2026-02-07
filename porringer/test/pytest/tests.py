"""Implementation of tests that should be overridden in plugins"""

import shutil
from abc import ABCMeta

import pytest
from porringer.core.plugin_schema.environment import Environment
from porringer.test.pytest.shared import (
    EnvironmentTests,
    PluginIntegrationTests,
    PluginUnitTests,
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
