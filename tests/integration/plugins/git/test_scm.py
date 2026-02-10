"""Integration tests for the Git SCM plugin."""

import pytest

from porringer.plugin.git.plugin import GitScm
from porringer.test.pytest.tests import ScmEnvironmentIntegrationTests


class TestGitScm(ScmEnvironmentIntegrationTests[GitScm]):
    """Integration tests for the Git SCM environment plugin."""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[GitScm]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the ScmEnvironment
        """
        return GitScm
