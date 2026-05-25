"""Helpers for test environment."""

"""Tests plugin schemas."""

import pytest

from porringer.plugin.brew.plugin import BrewEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[BrewEnvironment]):
    """The tests for the brew environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[BrewEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return BrewEnvironment
