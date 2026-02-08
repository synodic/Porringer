"""Tests for the npm project environment plugin."""

import pytest

from porringer.plugin.npm_project.plugin import NpmProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[NpmProjectEnvironment]):
    """Unit tests for the npm project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[NpmProjectEnvironment]:
        """Returns the npm project environment type."""
        return NpmProjectEnvironment
