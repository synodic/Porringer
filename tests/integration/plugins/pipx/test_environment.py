"""Tests plugin schemas"""

import pytest

from porringer.plugin.pipx.plugin import PipxEnvironment
from porringer.test.pytest.tests import EnvironmentIntegrationTests


class TestEnvironment(EnvironmentIntegrationTests[PipxEnvironment]):
    """The tests for the vcpkg provider"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PipxEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PipxEnvironment
