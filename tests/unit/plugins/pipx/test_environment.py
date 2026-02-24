"""Tests plugin schemas"""

import pytest

from porringer.plugin.pipx.plugin import PIPXEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[PIPXEnvironment]):
    """The tests for the pipx environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PIPXEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PIPXEnvironment
