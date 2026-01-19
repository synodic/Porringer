"""Test the click cli"""

from typer.testing import CliRunner

from porringer.console.entry import app


class TestCLI:
    """Tests for the typer CLI"""

    @staticmethod
    def test_version(test_config) -> None:
        """Verifies the version command works"""
        runner = CliRunner()
        result = runner.invoke(app, ['--version'], obj=test_config)

        assert result.exit_code == 0
