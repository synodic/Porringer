"""Helpers for test environment.

Tests for the Poetry project environment plugin.
"""

import pytest

from porringer.plugin.poetry.plugin import PoetryEnvironment
from porringer.test.pytest.tests import ProjectEnvironmentUnitTests


class TestEnvironment(ProjectEnvironmentUnitTests[PoetryEnvironment]):
    """Unit tests for the Poetry project environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[PoetryEnvironment]:
        """Returns the Poetry project environment type."""
        return PoetryEnvironment
