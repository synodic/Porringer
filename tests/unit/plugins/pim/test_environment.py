"""Tests plugin schemas for Python Install Manager (pim)"""

import pytest

from porringer.plugin.pim.plugin import PimEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[PimEnvironment]):
    """The tests for the pim environment plugin"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PimEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return PimEnvironment
