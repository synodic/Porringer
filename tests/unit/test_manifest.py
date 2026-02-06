"""Test the setup/manifest functionality in install command"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from porringer.api import API
from porringer.console.entry import app
from porringer.schema import Prerequisite, SetupActionType, SetupManifest, SetupParameters
from porringer.utility.exception import ManifestError

# Test constants
EXPECTED_ACTIONS_JSON_MANIFEST = 2  # 1 install + 1 command
EXPECTED_ACTIONS_WITH_PREREQUISITES = 4  # 1 check + 2 installs + 1 command

# Action indices
FIRST_ACTION_INDEX = 0
SECOND_ACTION_INDEX = 1
THIRD_ACTION_INDEX = 2
FOURTH_ACTION_INDEX = 3

# Exit codes
EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1

# Count constants
SINGLE_MANIFEST = 1
DUAL_MANIFESTS = 2
THREE_ACTIONS = 3
TWO_ACTIONS = 2
SINGLE_FAILED_PATH = 1
NO_FAILED_PATHS = 0


class TestPrerequisite:
    """Tests for Prerequisite platform filtering"""

    @staticmethod
    def test_is_applicable_no_platforms() -> None:
        """Prerequisite with no platforms should apply to all platforms"""
        prereq = Prerequisite(plugin='pip')
        assert prereq.is_applicable() is True

    @staticmethod
    def test_is_applicable_with_empty_platforms() -> None:
        """Prerequisite with empty platforms list should apply to all platforms"""
        prereq = Prerequisite(plugin='pip', platforms=[])
        assert prereq.is_applicable() is True

    @staticmethod
    def test_is_applicable_matching_platform() -> None:
        """Prerequisite should apply when current platform is in the list"""
        prereq = Prerequisite(plugin='test', platforms=[sys.platform])
        assert prereq.is_applicable() is True

    @staticmethod
    def test_is_applicable_non_matching_platform() -> None:
        """Prerequisite should not apply when current platform is not in the list"""
        prereq = Prerequisite(plugin='test', platforms=['nonexistent_platform'])
        assert prereq.is_applicable() is False

    @staticmethod
    def test_is_applicable_multiple_platforms_matching() -> None:
        """Prerequisite should apply when current platform is one of multiple"""
        prereq = Prerequisite(plugin='test', platforms=['win32', 'darwin', 'linux', sys.platform])
        assert prereq.is_applicable() is True

    @staticmethod
    def test_is_applicable_multiple_platforms_not_matching() -> None:
        """Prerequisite should not apply when current platform is not in multiple"""
        prereq = Prerequisite(plugin='test', platforms=['nonexistent1', 'nonexistent2'])
        assert prereq.is_applicable() is False


class TestSetupManifest:
    """Tests for manifest loading"""

    @staticmethod
    def test_load_json_manifest(test_api: API) -> None:
        """Test loading a porringer.json manifest"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'pip': ['requests']},
                'post_install': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.update.preview_single(Path(tmpdir))

            assert results.manifest_path == manifest_path
            # 1 install + 1 command = 2 actions
            assert len(results.actions) == EXPECTED_ACTIONS_JSON_MANIFEST

    @staticmethod
    def test_load_pyproject_manifest(test_api: API) -> None:
        """Test loading from pyproject.toml [tool.porringer]"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject_path = Path(tmpdir) / 'pyproject.toml'
            pyproject_content = """
[tool.porringer]
version = "1"
packages.pip = ["requests"]
"""
            pyproject_path.write_text(pyproject_content)

            results = test_api.update.preview_single(Path(tmpdir))

            assert results.manifest_path == pyproject_path
            assert len(results.actions) == 1  # 1 install

    @staticmethod
    def test_missing_manifest_raises_error(test_api: API) -> None:
        """Test that missing manifest raises ManifestError"""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            test_api.update.preview_single(Path(tmpdir))


class TestSetupPreview:
    """Tests for setup preview"""

    @staticmethod
    def test_preview_builds_actions(test_api: API) -> None:
        """Test that preview builds correct action types"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'prerequisites': [{'plugin': 'pip'}],
                'packages': {'pip': ['requests', 'pydantic']},
                'post_install': ['pdm install'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.update.preview_single(Path(tmpdir))

            # 1 check + 2 installs + 1 command = 4 actions
            assert len(results.actions) == EXPECTED_ACTIONS_WITH_PREREQUISITES

            action_types = [a.action_type for a in results.actions]
            assert action_types[FIRST_ACTION_INDEX] == SetupActionType.CHECK_PLUGIN
            assert action_types[SECOND_ACTION_INDEX] == SetupActionType.INSTALL_PACKAGE
            assert action_types[THIRD_ACTION_INDEX] == SetupActionType.INSTALL_PACKAGE
            assert action_types[FOURTH_ACTION_INDEX] == SetupActionType.RUN_COMMAND


class TestSetupBatch:
    """Tests for batch setup operations"""

    @staticmethod
    def test_preview_batch_single_path(test_api: API) -> None:
        """Test batch preview with a single path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'pip': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir))
            results = test_api.update.preview_batch(params)

            assert len(results.manifest_results) == 1
            assert results.total_actions == 1
            assert len(results.failed_paths) == 0

    @staticmethod
    def test_preview_batch_multiple_paths(test_api: API) -> None:
        """Test batch preview with multiple paths"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two project directories with manifests
            project1 = Path(tmpdir) / 'project1'
            project2 = Path(tmpdir) / 'project2'
            project1.mkdir()
            project2.mkdir()

            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'pip': ['requests']}}))
            (project2 / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'pip': ['flask', 'pytest']}})
            )

            params = SetupParameters(paths=[project1, project2])
            results = test_api.update.preview_batch(params)

            assert len(results.manifest_results) == DUAL_MANIFESTS
            assert results.total_actions == THREE_ACTIONS  # 1 + 2
            assert len(results.failed_paths) == NO_FAILED_PATHS

    @staticmethod
    def test_preview_batch_with_failures(test_api: API) -> None:
        """Test batch preview continues on manifest errors"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project1 = Path(tmpdir) / 'project1'
            project2 = Path(tmpdir) / 'project2'
            project1.mkdir()
            project2.mkdir()

            # Only project1 has a manifest
            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'pip': ['requests']}}))

            params = SetupParameters(paths=[project1, project2], fail_fast=False)
            results = test_api.update.preview_batch(params)

            assert len(results.manifest_results) == SINGLE_MANIFEST
            assert len(results.failed_paths) == SINGLE_FAILED_PATH
            assert results.failed_paths[FIRST_ACTION_INDEX][0] == project2

    @staticmethod
    def test_preview_batch_from_cache(test_api: API, temp_cache_dir) -> None:
        """Test batch preview using cached directories"""
        tmp_path, _ = temp_cache_dir
        project1 = tmp_path / 'project1'
        project1.mkdir()

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'pip': ['requests']}}))

        # Add to cache
        test_api.cache.add_directory(project1)

        # Preview from cache (paths=None)
        params = SetupParameters(paths=None)
        results = test_api.update.preview_batch(params)

        assert len(results.manifest_results) == SINGLE_MANIFEST
        assert results.total_actions == SINGLE_MANIFEST

    @staticmethod
    def test_preview_batch_from_all_cached(test_api: API, temp_cache_dir) -> None:
        """Test batch preview using all cached directories"""
        tmp_path, _ = temp_cache_dir
        project1 = tmp_path / 'project1'
        project2 = tmp_path / 'project2'
        project1.mkdir()
        project2.mkdir()

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'pip': ['requests']}}))
        (project2 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'pip': ['flask']}}))

        # Add directories to cache
        test_api.cache.add_directory(project1)
        test_api.cache.add_directory(project2)

        # Preview from all cached
        params = SetupParameters(paths=None)
        results = test_api.update.preview_batch(params)

        assert len(results.manifest_results) == DUAL_MANIFESTS
        assert results.total_actions == TWO_ACTIONS


class TestPackageSpec:
    """Tests for PackageSpec model and string/object coercion"""

    @staticmethod
    def test_manifest_coerces_string_packages() -> None:
        """String package entries are coerced to PackageSpec objects"""
        manifest = SetupManifest(packages={'pip': ['requests', 'flask']})
        assert len(manifest.packages['pip']) == 2
        assert manifest.packages['pip'][0].name == 'requests'
        assert manifest.packages['pip'][0].description is None

    @staticmethod
    def test_manifest_accepts_object_packages() -> None:
        """Object package entries are parsed as PackageSpec"""
        manifest = SetupManifest(packages={'pip': [{'name': 'ruff', 'description': 'Fast linter'}]})
        assert manifest.packages['pip'][0].name == 'ruff'
        assert manifest.packages['pip'][0].description == 'Fast linter'

    @staticmethod
    def test_manifest_mixed_string_and_object_packages() -> None:
        """Manifest accepts a mix of string and object package entries"""
        manifest = SetupManifest(
            packages={
                'pip': [
                    'requests',
                    {'name': 'ruff', 'description': 'Fast linter'},
                    'pytest',
                ]
            }
        )
        pkgs = manifest.packages['pip']
        assert len(pkgs) == 3
        assert pkgs[0].name == 'requests'
        assert pkgs[0].description is None
        assert pkgs[1].name == 'ruff'
        assert pkgs[1].description == 'Fast linter'
        assert pkgs[2].name == 'pytest'
        assert pkgs[2].description is None


class TestManifestMetadata:
    """Tests for manifest display metadata fields"""

    @staticmethod
    def test_metadata_in_json_manifest(test_api: API) -> None:
        """Test that display metadata is loaded from JSON and propagated to SetupResults"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'name': 'Dev Environment',
                'description': 'Tools for development',
                'author': 'Synodic',
                'url': 'https://example.com',
                'packages': {'pip': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.update.preview_single(Path(tmpdir))

            assert results.metadata is not None
            assert results.metadata.name == 'Dev Environment'
            assert results.metadata.description == 'Tools for development'
            assert results.metadata.author == 'Synodic'
            assert results.metadata.url == 'https://example.com/'

    @staticmethod
    def test_metadata_none_when_not_provided(test_api: API) -> None:
        """Test that metadata fields are None when not in manifest"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'pip': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.update.preview_single(Path(tmpdir))

            assert results.metadata is not None
            assert results.metadata.name is None
            assert results.metadata.description is None
            assert results.metadata.author is None
            assert results.metadata.url is None

    @staticmethod
    def test_package_description_in_actions(test_api: API) -> None:
        """Test that per-package descriptions propagate to SetupAction"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {
                    'pip': [
                        {'name': 'ruff', 'description': 'Fast linter'},
                        'pytest',
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.update.preview_single(Path(tmpdir))

            assert len(results.actions) == 2
            assert results.actions[0].package == 'ruff'
            assert results.actions[0].package_description == 'Fast linter'
            assert results.actions[1].package == 'pytest'
            assert results.actions[1].package_description is None


class TestSetupCLI:
    """Tests for setup CLI commands (now via install)"""

    @staticmethod
    def test_install_dry_run_api(test_api: API) -> None:
        """Test the install dry-run functionality via API"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'pip': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            # Test dry-run via API
            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.update.preview_batch(setup_params)
            results = test_api.update.execute_batch(preview, setup_params)

            # Should have 1 action for pip install
            assert len(results.manifest_results) == 1
            assert len(results.manifest_results[0].results) == 1
            # Should succeed in dry-run
            assert results.manifest_results[0].results[0].success

    @staticmethod
    def test_install_missing_manifest_error() -> None:
        """Test that missing manifest shows error in CLI"""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ['install', '--dry-run', '--path', tmpdir],
            )

            assert result.exit_code == EXIT_CODE_FAILURE
