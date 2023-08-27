"""Test the click cli"""
from click.testing import CliRunner

from porringer.application.click import Configuration, application


class TestCLI:
    """_summary_"""

    def test_version(self) -> None:
        """_summary_"""
        runner = CliRunner()
        config = Configuration()
        result = runner.invoke(application, ["--version"], obj=config)

        assert result.exit_code == 0
        assert result.output

    def test_self_check(self) -> None:
        """_summary_"""
        runner = CliRunner()
        config = Configuration()
        result = runner.invoke(application, ["self", "check"], obj=config)

        assert result.exit_code == 0
        assert not result.output

    def test_self_update(self) -> None:
        """_summary_"""
        runner = CliRunner()
        config = Configuration()
        result = runner.invoke(application, ["self", "update"], obj=config)

        assert result.exit_code == 0
        assert not result.output
