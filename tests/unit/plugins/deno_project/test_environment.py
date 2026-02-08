"""Tests for the deno project environment plugin."""

import pytest

from porringer.plugin.deno_project.plugin import DenoProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[DenoProjectEnvironment]):
    """Unit tests for the deno project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[DenoProjectEnvironment]:
        """Returns the deno project environment type."""
        return DenoProjectEnvironment
