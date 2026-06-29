"""Tests covering the shared behavior."""

"""Shared data between the exposed fixtures."""

from abc import ABCMeta, abstractmethod
from typing import LiteralString, cast

import pytest
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Plugin, PluginParameters
from porringer.test.pytest.variants import environment_variants, project_environment_variants, scm_environment_variants


class BaseTests[T: Plugin](metaclass=ABCMeta):
    """Shared testing information for all plugin test classes."""

    @abstractmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type(self) -> type[T]:
        """A required testing hook that allows type generation."""
        raise NotImplementedError('Override this fixture')

    @staticmethod
    @pytest.fixture(name='plugin_group_name', scope='session')
    def fixture_plugin_group_name() -> LiteralString:
        """Returns the plugin group name.

        Returns:
            str: The name of the plugin group.
        """
        return 'porringer'


class PluginTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Testing information for basic plugin test classes."""

    @staticmethod
    @pytest.fixture(
        name='plugin',
        scope='session',
    )
    def fixture_plugin(plugin_type: type[T], plugin_parameters: PluginParameters) -> T:
        """Overridden plugin generator for creating a populated data plugin type.

        Args:
            plugin_type: Plugin type
            plugin_parameters: Plugin parameters
        Returns:
            A newly constructed provider
        """
        plugin = plugin_type(plugin_parameters)

        return plugin


class PluginIntegrationTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Integration testing information for plugin test classes."""


class PluginUnitTests[T: Plugin](BaseTests[T], metaclass=ABCMeta):
    """Unit testing information for plugin test classes."""


class EnvironmentTests[T: Environment](PluginTests[T], metaclass=ABCMeta):
    """Shared functionality between the different testing categories."""

    @staticmethod
    @pytest.fixture(
        name='environment_type',
        scope='session',
        params=environment_variants,
    )
    def fixture_environment_type(request: pytest.FixtureRequest) -> type[Environment]:
        """Fixture defining all testable variations mock Environment.

        Args:
            request: Parameterization list

        Returns:
            Variation of a Environment
        """
        environment_type = cast(type[Environment], request.param)

        return environment_type


class ProjectEnvironmentTests[T: ProjectEnvironment](PluginTests[T], metaclass=ABCMeta):
    """Shared functionality between the different project-environment testing categories."""

    @staticmethod
    @pytest.fixture(
        name='project_environment_type',
        scope='session',
        params=project_environment_variants,
    )
    def fixture_project_environment_type(request: pytest.FixtureRequest) -> type[ProjectEnvironment]:
        """Fixture defining all testable variations of mock ProjectEnvironment.

        Args:
            request: Parameterization list

        Returns:
            Variation of a ProjectEnvironment
        """
        project_environment_type = cast(type[ProjectEnvironment], request.param)

        return project_environment_type


class ScmEnvironmentTests[T: ScmEnvironment](PluginTests[T], metaclass=ABCMeta):
    """Shared functionality between the different SCM-environment testing categories."""

    @staticmethod
    @pytest.fixture(
        name='scm_environment_type',
        scope='session',
        params=scm_environment_variants,
    )
    def fixture_scm_environment_type(request: pytest.FixtureRequest) -> type[ScmEnvironment]:
        """Fixture defining all testable variations of mock ScmEnvironment.

        Args:
            request: Parameterization list

        Returns:
            Variation of a ScmEnvironment
        """
        scm_environment_type = cast(type[ScmEnvironment], request.param)

        return scm_environment_type


class RuntimeProviderTests[T: Environment](PluginTests[T], metaclass=ABCMeta):
    """Shared functionality for plugins implementing ``RuntimeProvider``."""
