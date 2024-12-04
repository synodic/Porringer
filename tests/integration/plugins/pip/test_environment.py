"""Tests plugin schemas"""

import pytest

from porringer.plugin.pip.plugin import PipEnvironment
from porringer.test.pytest.tests import EnvironmentIntegrationTests


class TestEnvironment(EnvironmentIntegrationTests[PipEnvironment]):
    """The tests for the vcpkg provider"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PipEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PipEnvironment
