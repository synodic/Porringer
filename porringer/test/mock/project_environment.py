"""Tests covering the project environment behavior."""

"""Mock project environment data."""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem


class MockProjectEnvironment(ProjectEnvironment):
    """Mocked project environment plugin for testing."""

    _sync_verb: str = 'sync'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Mock manages the `python` ecosystem."""
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Mock consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Returns the mock tool name."""
        return 'mock-project'
