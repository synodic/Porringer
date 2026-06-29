"""Helpers for test environment."""

"""Tests for the PDM project environment plugin."""

import pytest

from porringer.plugin.pdm.plugin import PDMEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PDMEnvironment]):
    """Unit tests for the PDM project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PDMEnvironment]:
        """Returns the PDM project environment type."""
        return PDMEnvironment
