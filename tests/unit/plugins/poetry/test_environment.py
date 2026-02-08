"""Tests for the Poetry project environment plugin."""

import pytest

from porringer.plugin.poetry.plugin import PoetryProjectEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PoetryProjectEnvironment]):
    """Unit tests for the Poetry project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PoetryProjectEnvironment]:
        """Returns the Poetry project environment type."""
        return PoetryProjectEnvironment
