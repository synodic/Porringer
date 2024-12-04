"""Tests plugin schemas"""

import pytest

from porringer.plugin.winget.plugin import WingetEnvironment
from porringer.test.pytest.tests import EnvironmentIntegrationTests


class TestEnvironment(EnvironmentIntegrationTests[WingetEnvironment]):
    """The tests for the vcpkg provider"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[WingetEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return WingetEnvironment
