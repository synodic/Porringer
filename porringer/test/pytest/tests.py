"""Implementation of tests that should be overridden in plugins"""

from abc import ABCMeta

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
