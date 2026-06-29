"""Helpers for test environment."""

"""Tests plugin schemas."""

import pytest

from porringer.plugin.npm.plugin import NPMEnvironment
from porringer.test.pytest.tests import EnvironmentUnitTests


class TestEnvironment(EnvironmentUnitTests[NPMEnvironment]):
    """The tests for the npm environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[NPMEnvironment]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Environment
        """
        return NPMEnvironment
