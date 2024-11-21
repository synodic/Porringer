"""Test the click cli"""

from click.testing import CliRunner

from porringer.console.entry import Configuration, application


class TestCLI:
    """_summary_"""

    @staticmethod
    def test_version() -> None:
        """_summary_"""
        runner = CliRunner()
        config = Configuration()
        result = runner.invoke(application, ['--version'], obj=config)

        assert result.exit_code == 0
        assert result.output

    @staticmethod
    def test_verbosity() -> None:
        """Test's that the verbosity flag is implicitly capped at 3 levels"""
        runner = CliRunner()
        config = Configuration()

        result = runner.invoke(application, ['-vvv'], obj=config)

        assert result.exit_code == 0

        level = config.logger.level

        result = runner.invoke(application, ['-vvvv'], obj=config)

        assert result.exit_code == 0
        assert config.logger.level == level
