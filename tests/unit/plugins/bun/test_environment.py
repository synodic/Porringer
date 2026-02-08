"""Tests plugin schemas"""

import pytest

from porringer.plugin.bun.plugin import BunEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[BunEnvironment]):
    """The tests for the bun environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[BunEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return BunEnvironment
