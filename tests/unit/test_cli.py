"""Test the click cli"""

from typer.testing import CliRunner

from porringer.console.entry import app
from porringer.console.schema import Configuration


class TestCLI:
    """Tests for the typer CLI"""

    @staticmethod
    def test_version() -> None:
        """Verifies the version command works"""
        runner = CliRunner()
        config = Configuration()
        result = runner.invoke(app, ['--version'], obj=config)

        assert result.exit_code == 0, result.output
