"""Helpers for test environment.

Tests for the yarn project environment plugin.
"""

import pytest

from porringer.plugin.yarn_project.plugin import YarnProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[YarnProjectEnvironment]):
    """Unit tests for the yarn project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[YarnProjectEnvironment]:
        """Returns the yarn project environment type."""
        return YarnProjectEnvironment
