"""Test the click cli"""

import logging
from unittest.mock import patch

import pytest
from packaging.version import Version
from typer.testing import CliRunner

from porringer.console.entry import app
from porringer.core.schema import PluginKind
from porringer.schema import PluginInfo

# A minimal fake result so that ``plugin list`` never spawns subprocesses.
_FAKE_PLUGINS = [
    PluginInfo(name='stub', kind=PluginKind.PACKAGE, version=Version('0.1.0'), installed=True, tool_version=None)
]


class TestCLI:
    """Tests for the typer CLI"""

    @staticmethod
    def test_version(test_config) -> None:
        """Verifies the version command works"""
        runner = CliRunner()
        result = runner.invoke(app, ['--version'], obj=test_config)

        assert result.exit_code == 0


class TestCLILoggingLevels:
    """Verify --verbose and --debug flags wire to logger.setLevel."""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _reset_logger():
        """Ensure the porringer logger starts and ends with a clean slate."""
        logger = logging.getLogger('porringer')
        old_propagate = logger.propagate
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        yield
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = old_propagate

    @pytest.fixture(autouse=True)
    @staticmethod
    def _mock_plugin_list():
        """Bypass real plugin discovery so the tests run in milliseconds."""
        with patch('porringer.backend.command.plugin.PluginCommands.list', return_value=_FAKE_PLUGINS):
            yield

    @staticmethod
    def test_default_level_is_warning() -> None:
        """With no flags the porringer logger is set to WARNING."""
        runner = CliRunner()
        result = runner.invoke(app, ['plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.WARNING

    @staticmethod
    def test_verbose_sets_info() -> None:
        """A single -v sets the logger to INFO."""
        runner = CliRunner()
        result = runner.invoke(app, ['-v', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.INFO

    @staticmethod
    def test_double_verbose_sets_debug() -> None:
        """-vv sets the logger to DEBUG."""
        runner = CliRunner()
        result = runner.invoke(app, ['-vv', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.DEBUG

    @staticmethod
    def test_debug_flag_sets_debug() -> None:
        """--debug sets the logger to DEBUG."""
        runner = CliRunner()
        result = runner.invoke(app, ['--debug', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.DEBUG
