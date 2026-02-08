"""Tests plugin schemas"""

import pytest

from porringer.plugin.deno.plugin import DenoEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[DenoEnvironment]):
    """The tests for the deno environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[DenoEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return DenoEnvironment
