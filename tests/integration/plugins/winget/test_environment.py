"""Tests plugin schemas"""

import pytest
from pytest_porringer.tests import EnvironmentIntegrationTests

from porringer_winget.plugin import WingetEnvironment


class TestEnvironment(EnvironmentIntegrationTests[WingetEnvironment]):
    """The tests for the vcpkg provider"""

    @pytest.fixture(name="plugin_type", scope="session")
    def fixture_plugin_type(self) -> type[WingetEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return WingetEnvironment
