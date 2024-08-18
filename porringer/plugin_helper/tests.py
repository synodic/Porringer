"""Implementation of tests that should be overridden in plugins"""

from abc import ABCMeta
from typing import Generic

from porringer_core.plugin_schema.environment import EnvironmentT
from synodic_utilities.utility import canonicalize_type

from pytest_porringer.shared import (
    EnvironmentTests,
    PluginIntegrationTests,
    PluginUnitTests,
)


class EnvironmentIntegrationTests(
    PluginIntegrationTests[EnvironmentT], EnvironmentTests[EnvironmentT], Generic[EnvironmentT], metaclass=ABCMeta
):
    """Base class for all environment integration tests that test plugin agnostic behavior"""

    def test_group_name(self, plugin_type: type[EnvironmentT]) -> None:
        """Verifies that the group name is the same as the plugin type

        Args:
            plugin_type: The type to register
        """
        assert canonicalize_type(plugin_type).group == "environment"


class EnvironmentUnitTests(
    PluginUnitTests[EnvironmentT], EnvironmentTests[EnvironmentT], Generic[EnvironmentT], metaclass=ABCMeta
):
    """Custom implementations of the environment class should inherit from this class for its tests.
    Base class for all environment unit tests that test plugin agnostic behavior
    """
