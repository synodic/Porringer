"""Tests plugin schemas"""

import pytest

from porringer.plugin.pip.plugin import PIPEnvironment
from porringer.test.pytest.tests import EnvironmentIntegrationTests


class TestEnvironment(EnvironmentIntegrationTests[PIPEnvironment]):
    """The tests for the pip environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PIPEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PIPEnvironment
