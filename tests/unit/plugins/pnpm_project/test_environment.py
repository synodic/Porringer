"""Helpers for test environment."""

"""Tests for the pnpm project environment plugin."""

import pytest

from porringer.plugin.pnpm_project.plugin import PNPMProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PNPMProjectEnvironment]):
    """Unit tests for the pnpm project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PNPMProjectEnvironment]:
        """Returns the pnpm project environment type."""
        return PNPMProjectEnvironment
