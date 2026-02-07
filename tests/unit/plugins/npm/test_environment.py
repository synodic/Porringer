"""Tests plugin schemas"""

import pytest

from porringer.plugin.npm.plugin import NpmEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[NpmEnvironment]):
    """The tests for the npm environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[NpmEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return NpmEnvironment
