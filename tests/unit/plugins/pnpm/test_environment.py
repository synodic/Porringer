"""Helpers for test environment."""

"""Tests plugin schemas."""

import pytest

from porringer.plugin.pnpm.plugin import PNPMEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[PNPMEnvironment]):
    """The tests for the pnpm environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PNPMEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return PNPMEnvironment
