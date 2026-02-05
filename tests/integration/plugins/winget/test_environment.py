"""Tests plugin schemas"""

import shutil
import sys

import pytest

from porringer.plugin.winget.plugin import WingetEnvironment
from porringer.test.pytest.tests import EnvironmentIntegrationTests


class TestEnvironment(EnvironmentIntegrationTests[WingetEnvironment]):
    """The tests for the winget provider"""

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[WingetEnvironment]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Environment
        """
        return WingetEnvironment

    @staticmethod
    @pytest.mark.skipif(sys.platform != 'win32', reason='winget only available on Windows')
    def test_is_available() -> None:
        """Verifies that winget can detect its own presence on Windows.

        This test confirms that WingetEnvironment.is_available() correctly
        identifies whether winget is installed on the system.
        """
        # On Windows, winget should typically be available
        # The test verifies the method runs without error and returns a boolean
        result = WingetEnvironment.is_available()
        assert isinstance(result, bool)
        # If winget is installed (common on modern Windows), this should be True
        # We don't assert True because CI environments may not have winget
        if result:
            # Additional verification: if available, the command should exist
            assert shutil.which('winget') is not None
