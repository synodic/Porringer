"""Tests for the pnpm project environment plugin."""

import pytest

from porringer.plugin.pnpm_project.plugin import PnpmProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PnpmProjectEnvironment]):
    """Unit tests for the pnpm project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PnpmProjectEnvironment]:
        """Returns the pnpm project environment type."""
        return PnpmProjectEnvironment
