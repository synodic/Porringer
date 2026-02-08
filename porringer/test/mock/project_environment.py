"""Mock project environment data"""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class MockProjectEnvironment(ProjectEnvironment):
    """Mocked project environment plugin for testing."""

    _sync_verb: str = 'sync'

    @staticmethod
    @override
    def package_backend() -> str:
        """Mock manages the ``python-project`` backend."""
        return 'python-project'

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
