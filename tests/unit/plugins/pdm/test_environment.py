"""Tests for the PDM project environment plugin."""

import pytest

from porringer.plugin.pdm.plugin import PdmProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PdmProjectEnvironment]):
    """Unit tests for the PDM project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PdmProjectEnvironment]:
        """Returns the PDM project environment type."""
        return PdmProjectEnvironment
