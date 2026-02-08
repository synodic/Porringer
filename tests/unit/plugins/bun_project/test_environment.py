"""Tests for the bun project environment plugin."""

import pytest

from porringer.plugin.bun_project.plugin import BunProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[BunProjectEnvironment]):
    """Unit tests for the bun project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[BunProjectEnvironment]:
        """Returns the bun project environment type."""
        return BunProjectEnvironment
