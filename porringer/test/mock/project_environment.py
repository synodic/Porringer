"""Mock project environment data"""

from typing import override

from porringer.core.plugin_schema.project_environment import ProjectEnvironment


class MockProjectEnvironment(ProjectEnvironment):
    """Mocked project environment plugin for testing."""

    _sync_verb: str = 'sync'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Returns the mock tool name."""
        return 'mock-project'
