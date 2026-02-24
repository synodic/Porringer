"""Tests plugin schemas"""

import pytest

from porringer.plugin.apt.plugin import APTEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[APTEnvironment]):
    """The tests for the apt environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[APTEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return APTEnvironment
