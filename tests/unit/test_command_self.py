"""Test the click cli command 'self' """
from click.testing import CliRunner

from porringer.application.click import Configuration, application


class TestCommandSelf:
    """_summary_"""

    def test_self_update_check(self) -> None:
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
