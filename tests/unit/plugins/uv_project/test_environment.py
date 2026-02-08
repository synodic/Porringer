"""Tests for the UV project environment plugin."""

import pytest
from porringer.plugin.uv_project.plugin import UvProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[UvProjectEnvironment]):
    """Unit tests for the UV project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[UvProjectEnvironment]:
        """Returns the UV project environment type."""
        return UvProjectEnvironment
