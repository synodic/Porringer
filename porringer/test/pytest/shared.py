"""Shared data between the exposed fixtures"""

from abc import ABCMeta, abstractmethod
from typing import LiteralString, cast

import pytest
from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Plugin, PluginParameters
from porringer.test.pytest.variants import environment_variants


class BaseTests[T: Plugin](metaclass=ABCMeta):
    """Shared testing information for all plugin test classes."""

    @abstractmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type(self) -> type[T]:
        """A required testing hook that allows type generation"""
        raise NotImplementedError('Override this fixture')

    @staticmethod
    @pytest.fixture(name='plugin_group_name', scope='session')
    def fixture_plugin_group_name() -> LiteralString:
        """Returns the plugin group name.

        Returns:
            str: The name of the plugin group.
        """
        return 'porringer'


class BaseIntegrationTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Integration testing information for all plugin test classes"""


class BaseUnitTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Unit testing information for all plugin test classes"""


class PluginTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Testing information for basic plugin test classes."""

    @staticmethod
    @pytest.fixture(
        name='plugin',
        scope='session',
    )
    def fixture_plugin(plugin_type: type[T], plugin_parameters: PluginParameters) -> T:
        """Overridden plugin generator for creating a populated data plugin type

        Args:
            plugin_type: Plugin type
            plugin_parameters: Plugin parameters
        Returns:
            A newly constructed provider
        """
        plugin = plugin_type(plugin_parameters)

        return plugin


class PluginIntegrationTests[T: Plugin](BaseIntegrationTests[T], metaclass=ABCMeta):
    """Integration testing information for basic plugin test classes"""


class PluginUnitTests[T: Plugin](BaseUnitTests[T], metaclass=ABCMeta):
    """Unit testing information for basic plugin test classes"""


class EnvironmentTests[T: Environment](PluginTests[T], metaclass=ABCMeta):
    """Shared functionality between the different testing categories"""

    @staticmethod
    @pytest.fixture(
        name='environment_type',
        scope='session',
        params=environment_variants,
    )
    def fixture_environment_type(request: pytest.FixtureRequest) -> type[Environment]:
        """Fixture defining all testable variations mock Environment

        Args:
            request: Parameterization list

        Returns:
            Variation of a Environment
        """
        environment_type = cast(type[Environment], request.param)

        return environment_type
