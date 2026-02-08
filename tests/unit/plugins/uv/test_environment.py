"""Tests plugin schemas"""

import pytest

from porringer.plugin.uv.plugin import UvEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[UvEnvironment]):
    """The tests for the uv environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[UvEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return UvEnvironment
