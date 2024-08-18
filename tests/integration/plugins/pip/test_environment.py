"""Tests plugin schemas"""

import pytest
from pytest_porringer.tests import EnvironmentIntegrationTests

from porringer_pip.plugin import PipEnvironment


class TestEnvironment(EnvironmentIntegrationTests[PipEnvironment]):
    """The tests for the vcpkg provider"""

    @pytest.fixture(name="plugin_type", scope="session")
    def fixture_plugin_type(self) -> type[PipEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PipEnvironment
