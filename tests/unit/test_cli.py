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

    def test_verbosity(self) -> None:
        """Test's that the verbosity flag is implicitly capped at 3 levels"""
        runner = CliRunner()
        config = Configuration()

        result = runner.invoke(application, ["-vvv"], obj=config)

        assert result.exit_code == 0

        level = config.logger.level

        result = runner.invoke(application, ["-vvvv"], obj=config)

        assert result.exit_code == 0
        assert config.logger.level == level
