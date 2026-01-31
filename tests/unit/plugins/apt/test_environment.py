"""Tests plugin schemas for APT (Advanced Package Tool)"""

import pytest

from porringer.plugin.apt.plugin import AptEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[AptEnvironment]):
    """The tests for the apt environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[AptEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return AptEnvironment
