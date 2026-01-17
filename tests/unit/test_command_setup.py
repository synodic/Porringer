"""Test the setup command"""

import json
import tempfile
from logging import Logger
from pathlib import Path

import pytest
from typer.testing import CliRunner

from porringer.api import API
from porringer.console.entry import app
from porringer.console.schema import Configuration
from porringer.schema import (
    APIParameters,
    LocalConfiguration,
    SetupActionType,
    SetupParameters,
)
from porringer.utility.exception import ManifestError

# Test constants
EXPECTED_ACTIONS_JSON_MANIFEST = 2  # 1 install + 1 command
EXPECTED_ACTIONS_WITH_PREREQUISITES = 4  # 1 check + 2 installs + 1 command


class TestSetupManifest:
    """Tests for manifest loading"""

    @staticmethod
    def test_load_json_manifest() -> None:
        """Test loading a porringer.json manifest"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'pip': ['requests']},
                'post_install': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(path=Path(tmpdir))
            results = api.setup.preview(params)

            assert results.manifest_path == manifest_path
            # 1 install + 1 command = 2 actions
            assert len(results.actions) == EXPECTED_ACTIONS_JSON_MANIFEST

    @staticmethod
    def test_load_pyproject_manifest() -> None:
        """Test loading from pyproject.toml [tool.porringer]"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject_path = Path(tmpdir) / 'pyproject.toml'
            pyproject_content = """
[tool.porringer]
version = "1"
packages.pip = ["requests"]
"""
            pyproject_path.write_text(pyproject_content)

            params = SetupParameters(path=Path(tmpdir))
            results = api.setup.preview(params)

            assert results.manifest_path == pyproject_path
            assert len(results.actions) == 1  # 1 install

    @staticmethod
    def test_missing_manifest_raises_error() -> None:
        """Test that missing manifest raises ManifestError"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        with tempfile.TemporaryDirectory() as tmpdir:
            params = SetupParameters(path=Path(tmpdir))

            with pytest.raises(ManifestError):
                api.setup.preview(params)


class TestSetupPreview:
    """Tests for setup preview"""

    @staticmethod
    def test_preview_builds_actions() -> None:
        """Test that preview builds correct action types"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'prerequisites': [{'plugin': 'pip'}],
                'packages': {'pip': ['requests', 'pydantic']},
                'post_install': ['pdm install'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(path=Path(tmpdir))
            results = api.setup.preview(params)

            # 1 check + 2 installs + 1 command = 4 actions
            assert len(results.actions) == EXPECTED_ACTIONS_WITH_PREREQUISITES

            action_types = [a.action_type for a in results.actions]
            assert action_types[0] == SetupActionType.CHECK_PLUGIN
            assert action_types[1] == SetupActionType.INSTALL_PACKAGE
            assert action_types[2] == SetupActionType.INSTALL_PACKAGE
            assert action_types[3] == SetupActionType.RUN_COMMAND


class TestSetupCLI:
    """Tests for setup CLI commands"""

    @staticmethod
    def test_setup_preview_command() -> None:
        """Test the setup preview CLI command"""
        runner = CliRunner()
        config = Configuration()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'pip': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = runner.invoke(app, ['setup', tmpdir], obj=config)

            assert result.exit_code == 0, result.output
            assert 'Setup Actions' in result.output

    @staticmethod
    def test_setup_missing_manifest_error() -> None:
        """Test that missing manifest shows error in CLI"""
        runner = CliRunner()
        config = Configuration()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ['setup', tmpdir], obj=config)

            assert result.exit_code == 1
            assert 'Error' in result.output
