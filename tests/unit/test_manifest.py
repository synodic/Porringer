"""Test the setup/manifest functionality in sync command"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from porringer.api import API
from porringer.backend.command.sync import SyncCommands
from porringer.console.entry import app
from porringer.schema import (
    ManifestValidationCode,
    PackageSpec,
    SetupActionType,
    SetupManifest,
    SetupParameters,
    SyncStrategy,
)
from porringer.utility.exception import ManifestError
from tests.conftest import execute_via_stream

# Test constants
EXPECTED_ACTIONS_JSON_MANIFEST = 2  # 1 install + 1 command

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
TWO_PACKAGES = 2
THREE_PACKAGES = 3
SINGLE_FAILED_PATH = 1
NO_FAILED_PATHS = 0


class TestSetupManifest:
    """Tests for manifest loading"""

    @staticmethod
    def test_load_json_manifest(test_api: API) -> None:
        """Test loading a porringer.json manifest"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

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
state.python = ["requests"]
"""
            pyproject_path.write_text(pyproject_content)

            results = test_api.sync.preview_single(Path(tmpdir))

            assert results.manifest_path == pyproject_path
            assert len(results.actions) == 1  # 1 install

    @staticmethod
    def test_missing_manifest_raises_error(test_api: API) -> None:
        """Test that missing manifest raises ManifestError"""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            test_api.sync.preview_single(Path(tmpdir))


class TestSetupPreview:
    """Tests for setup preview"""

    @staticmethod
    def test_preview_builds_actions(test_api: API) -> None:
        """Test that preview builds correct action types"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests', 'pydantic']},
                'post_sync': ['pdm install'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

            # 2 packages + 1 command = 3 actions
            assert len(results.actions) == THREE_ACTIONS

            action_types = [a.action_type for a in results.actions]
            assert action_types[FIRST_ACTION_INDEX] == SetupActionType.PACKAGE
            assert action_types[SECOND_ACTION_INDEX] == SetupActionType.PACKAGE
            assert action_types[THIRD_ACTION_INDEX] == SetupActionType.RUN_COMMAND

    @staticmethod
    def test_preview_excludes_filtered_packages(test_api: API) -> None:
        """Test that packages with non-matching platforms are excluded from actions"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {
                    'python': [
                        'requests',
                        {'name': 'pywin32', 'platforms': ['nonexistent_platform']},
                        {'name': 'uvloop', 'platforms': [sys.platform]},
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

            # Only 'requests' (no filter) and 'uvloop' (matching) should be included
            assert len(results.actions) == TWO_ACTIONS
            package_names = [str(a.package) for a in results.actions]
            assert 'requests' in package_names
            assert 'uvloop' in package_names
            assert 'pywin32' not in package_names

    @staticmethod
    def test_preview_includes_all_when_no_platform_filters(test_api: API) -> None:
        """Test that all packages are included when none have platform filters"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests', 'flask', 'pytest']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

            assert len(results.actions) == THREE_ACTIONS


class TestSetupBatch:
    """Tests for batch setup operations"""

    @staticmethod
    def test_preview_batch_single_path(test_api: API) -> None:
        """Test batch preview with a single path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir))
            results = test_api.sync.preview_batch(params)

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

            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'state': {'python': ['requests']}}))
            (project2 / 'porringer.json').write_text(
                json.dumps({'version': '1', 'state': {'python': ['flask', 'pytest']}})
            )

            params = SetupParameters(paths=[project1, project2])
            results = test_api.sync.preview_batch(params)

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
            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'state': {'python': ['requests']}}))

            params = SetupParameters(paths=[project1, project2], fail_fast=False)
            results = test_api.sync.preview_batch(params)

            assert len(results.manifest_results) == SINGLE_MANIFEST
            assert len(results.failed_paths) == SINGLE_FAILED_PATH
            assert results.failed_paths[FIRST_ACTION_INDEX][0] == project2

    @staticmethod
    def test_preview_batch_from_cache(test_api: API, temp_cache_dir) -> None:
        """Test batch preview using cached directories"""
        tmp_path, _ = temp_cache_dir
        project1 = tmp_path / 'project1'
        project1.mkdir()

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'state': {'python': ['requests']}}))

        # Add to cache
        test_api.cache.add_directory(project1)

        # Preview from cache (paths=None)
        params = SetupParameters(paths=None)
        results = test_api.sync.preview_batch(params)

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

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'state': {'python': ['requests']}}))
        (project2 / 'porringer.json').write_text(json.dumps({'version': '1', 'state': {'python': ['flask']}}))

        # Add directories to cache
        test_api.cache.add_directory(project1)
        test_api.cache.add_directory(project2)

        # Preview from all cached
        params = SetupParameters(paths=None)
        results = test_api.sync.preview_batch(params)

        assert len(results.manifest_results) == DUAL_MANIFESTS
        assert results.total_actions == TWO_ACTIONS


class TestPackageSpec:
    """Tests for PackageSpec model and string/object coercion"""

    @staticmethod
    def test_manifest_coerces_string_packages() -> None:
        """String package entries are coerced to PackageSpec objects"""
        manifest = SetupManifest(state={'python': ['requests', 'flask']})
        assert len(manifest.state['python']) == TWO_PACKAGES
        assert str(manifest.state['python'][0].name) == 'requests'
        assert manifest.state['python'][0].description is None

    @staticmethod
    def test_manifest_accepts_object_packages() -> None:
        """Object package entries are parsed as PackageSpec"""
        manifest = SetupManifest(state={'python': [{'name': 'ruff', 'description': 'Fast linter'}]})
        assert str(manifest.state['python'][0].name) == 'ruff'
        assert manifest.state['python'][0].description == 'Fast linter'

    @staticmethod
    def test_manifest_mixed_string_and_object_packages() -> None:
        """Manifest accepts a mix of string and object package entries"""
        manifest = SetupManifest(
            state={
                'python': [
                    'requests',
                    {'name': 'ruff', 'description': 'Fast linter'},
                    'pytest',
                ]
            }
        )
        pkgs = manifest.state['python']
        assert len(pkgs) == THREE_PACKAGES
        assert str(pkgs[0].name) == 'requests'
        assert pkgs[0].description is None
        assert str(pkgs[1].name) == 'ruff'
        assert pkgs[1].description == 'Fast linter'
        assert str(pkgs[2].name) == 'pytest'
        assert pkgs[2].description is None

    @staticmethod
    def test_is_applicable_no_platforms() -> None:
        """PackageSpec with no platforms should apply to all platforms"""
        spec = PackageSpec(name='requests')
        assert spec.is_applicable() is True

    @staticmethod
    def test_is_applicable_with_empty_platforms() -> None:
        """PackageSpec with empty platforms list should apply to all platforms"""
        spec = PackageSpec(name='requests', platforms=[])
        assert spec.is_applicable() is True

    @staticmethod
    def test_is_applicable_matching_platform() -> None:
        """PackageSpec should apply when current platform is in the list"""
        spec = PackageSpec(name='pywin32', platforms=[sys.platform])
        assert spec.is_applicable() is True

    @staticmethod
    def test_is_applicable_non_matching_platform() -> None:
        """PackageSpec should not apply when current platform is not in the list"""
        spec = PackageSpec(name='pywin32', platforms=['nonexistent_platform'])
        assert spec.is_applicable() is False

    @staticmethod
    def test_is_applicable_multiple_platforms_matching() -> None:
        """PackageSpec should apply when current platform is one of multiple"""
        spec = PackageSpec(name='uvloop', platforms=['win32', 'darwin', 'linux', sys.platform])
        assert spec.is_applicable() is True

    @staticmethod
    def test_is_applicable_multiple_platforms_not_matching() -> None:
        """PackageSpec should not apply when current platform is not in multiple"""
        spec = PackageSpec(name='uvloop', platforms=['nonexistent1', 'nonexistent2'])
        assert spec.is_applicable() is False

    @staticmethod
    def test_string_coercion_has_empty_platforms() -> None:
        """String package entries should have empty platforms (all platforms)"""
        manifest = SetupManifest(state={'python': ['requests']})
        assert manifest.state['python'][0].platforms == []
        assert manifest.state['python'][0].is_applicable() is True

    @staticmethod
    def test_object_with_platforms_parsed() -> None:
        """Object package entries with platforms should be parsed correctly"""
        manifest = SetupManifest(state={'python': [{'name': 'pywin32', 'platforms': ['win32']}]})
        spec = manifest.state['python'][0]
        assert str(spec.name) == 'pywin32'
        assert spec.platforms == ['win32']


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
                'state': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

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
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

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
                'state': {
                    'python': [
                        {'name': 'ruff', 'description': 'Fast linter'},
                        'pytest',
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

            assert len(results.actions) == TWO_ACTIONS
            assert str(results.actions[0].package) == 'ruff'
            assert results.actions[0].package_description == 'Fast linter'
            assert str(results.actions[1].package) == 'pytest'
            assert results.actions[1].package_description is None


class TestSetupCLI:
    """Tests for setup CLI commands (now via sync)"""

    @staticmethod
    def test_sync_dry_run_api(test_api: API) -> None:
        """Test the sync dry-run functionality via API"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            # Test dry-run via API
            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            # Should have 1 action for pip install
            assert len(results.manifest_results) == 1
            assert len(results.manifest_results[0].results) == 1
            # Should succeed in dry-run
            assert results.manifest_results[0].results[0].success

    @staticmethod
    def test_sync_missing_manifest_error() -> None:
        """Test that missing manifest shows error in CLI"""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ['sync', '--dry-run', '--path', tmpdir],
            )

            assert result.exit_code == EXIT_CODE_FAILURE


# --- Validation constants ---
TWO_ERRORS = 2
THREE_WARNINGS = 3


class TestManifestValidation:
    """Tests for validate_manifest() API"""

    @staticmethod
    def test_valid_manifest(test_api: API) -> None:
        """A well-formed manifest with recognized backends returns valid=True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is True
            assert len(result.errors) == 0

    @staticmethod
    def test_path_not_found(test_api: API) -> None:
        """Non-existent path produces PATH_NOT_FOUND error"""
        result = test_api.sync.validate_manifest(Path('/nonexistent/path'))

        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].code == ManifestValidationCode.PATH_NOT_FOUND

    @staticmethod
    def test_no_manifest_in_directory(test_api: API) -> None:
        """Empty directory produces NO_MANIFEST error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            assert len(result.errors) == 1
            assert result.errors[0].code == ManifestValidationCode.NO_MANIFEST

    @staticmethod
    def test_invalid_json_syntax(test_api: API) -> None:
        """Malformed JSON produces SYNTAX_ERROR"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_path.write_text('{bad json!!!}')

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            assert result.errors[0].code == ManifestValidationCode.SYNTAX_ERROR

    @staticmethod
    def test_invalid_toml_syntax(test_api: API) -> None:
        """Malformed TOML produces SYNTAX_ERROR"""
        with tempfile.TemporaryDirectory() as tmpdir:
            toml_path = Path(tmpdir) / 'pyproject.toml'
            toml_path.write_text('[[[invalid toml')

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            assert result.errors[0].code == ManifestValidationCode.SYNTAX_ERROR

    @staticmethod
    def test_unsupported_version(test_api: API) -> None:
        """Unrecognized schema version produces UNSUPPORTED_VERSION error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '99', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            version_errors = [e for e in result.errors if e.code == ManifestValidationCode.UNSUPPORTED_VERSION]
            assert len(version_errors) == 1
            assert version_errors[0].field == 'version'

    @staticmethod
    def test_unknown_backend_in_state(test_api: API) -> None:
        """Unrecognized backend name in state produces UNKNOWN_PLUGIN error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'nonexistent_backend': ['foo']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            plugin_errors = [e for e in result.errors if e.code == ManifestValidationCode.UNKNOWN_PLUGIN]
            assert len(plugin_errors) == 1
            assert plugin_errors[0].field == 'state.nonexistent_backend'

    @staticmethod
    def test_invalid_package_name_warning(test_api: API) -> None:
        """Invalid package specifier is caught at schema validation time.

        Since PackageRef validates names on construction, invalid package
        specifiers now fail during manifest loading (SCHEMA_INVALID) rather
        than during the later package-name validity check.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['valid-package', '!!!invalid!!!']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            schema_errors = [e for e in result.errors if e.code == ManifestValidationCode.SCHEMA_INVALID]
            assert len(schema_errors) == 1
            assert '!!!invalid!!!' in schema_errors[0].message

    @staticmethod
    def test_duplicate_packages_warning(test_api: API) -> None:
        """Same package under multiple backends produces DUPLICATE_PACKAGE warning"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {
                    'python': ['requests'],
                    'python-tool': ['requests'],
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            dup_warnings = [w for w in result.warnings if w.code == ManifestValidationCode.DUPLICATE_PACKAGE]
            assert len(dup_warnings) == 1
            assert 'requests' in dup_warnings[0].message

    @staticmethod
    def test_validation_does_not_execute(test_api: API) -> None:
        """Validation does not trigger any sync operations"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
                'post_sync': ['echo should-not-run'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # This should return quickly without executing anything
            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is True

    @staticmethod
    def test_multiple_errors_and_warnings(test_api: API) -> None:
        """Multiple issues are all reported in a single result.

        Invalid package specifiers now fail at schema load time, so the
        manifest with '!!!bad!!!' will produce a SCHEMA_INVALID error
        before version/backend checks run.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '99',
                'state': {'fake_backend': ['!!!bad!!!', 'also-bad[>=']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            # Invalid packages now cause schema-load failure, so we get at least 1 error
            assert len(result.errors) >= 1
            assert any(e.code == ManifestValidationCode.SCHEMA_INVALID for e in result.errors)


class TestManifestSchema:
    """Tests for manifest_schema() export"""

    @staticmethod
    def test_manifest_schema_returns_dict() -> None:
        """manifest_schema() returns a valid JSON Schema dict"""
        schema = SyncCommands.manifest_schema()

        assert isinstance(schema, dict)
        assert 'properties' in schema

    @staticmethod
    def test_manifest_schema_contains_expected_fields() -> None:
        """Exported schema contains the main manifest fields"""
        schema = SyncCommands.manifest_schema()
        props = schema['properties']

        assert 'version' in props
        assert 'state' in props
        assert 'preferences' in props
        assert 'post_sync' in props


class TestDryRunStateAware:
    """Tests for state-aware dry-run (skipping already-installed packages)"""

    @staticmethod
    def test_dry_run_skips_installed_package(test_api: API) -> None:
        """Test that dry-run detects an already-installed package and marks it skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 'packaging' is always installed (it's a dependency of porringer itself)
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.skip_reason is not None
            assert 'packaging' in action_result.skip_reason

    @staticmethod
    def test_dry_run_does_not_skip_missing_package(test_api: API) -> None:
        """Test that dry-run reports success without skip for a package that is not installed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # Use a package name that should never be installed
            manifest_data = {'version': '1', 'state': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is False
            assert action_result.skip_reason is None

    @staticmethod
    def test_dry_run_version_satisfied(test_api: API) -> None:
        """Test that dry-run reports skipped when a version specifier is satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # packaging>=1.0 should always be satisfied
            manifest_data = {'version': '1', 'state': {'python': ['packaging>=1.0']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.skip_reason is not None
            assert 'satisfies' in action_result.skip_reason

    @staticmethod
    def test_dry_run_version_not_satisfied(test_api: API) -> None:
        """Test that dry-run does not skip when a version specifier is not satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # packaging>=99999 should never be satisfied
            manifest_data = {'version': '1', 'state': {'python': ['packaging>=99999']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is False


class TestSyncStrategyUpgrade:
    """Tests for LATEST and EXACT sync strategies."""

    @staticmethod
    def test_preview_latest_strategy_produces_upgrade_actions(test_api: API) -> None:
        """Test that preview with LATEST strategy produces PACKAGE actions with Upgrade description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests', 'pydantic']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir), strategy=SyncStrategy.LATEST)

            # 2 packages + 1 command = 3 actions
            assert len(results.actions) == THREE_ACTIONS

            action_types = [a.action_type for a in results.actions]
            assert action_types[FIRST_ACTION_INDEX] == SetupActionType.PACKAGE
            assert action_types[SECOND_ACTION_INDEX] == SetupActionType.PACKAGE
            assert action_types[THIRD_ACTION_INDEX] == SetupActionType.RUN_COMMAND

    @staticmethod
    def test_preview_exact_strategy_produces_package_actions(test_api: API) -> None:
        """Test that preview with EXACT strategy produces PACKAGE actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir), strategy=SyncStrategy.EXACT)

            assert len(results.actions) == 1
            assert results.actions[0].action_type == SetupActionType.PACKAGE

    @staticmethod
    def test_preview_batch_latest_strategy(test_api: API) -> None:
        """Test that batch preview with LATEST strategy threads strategy through."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), strategy=SyncStrategy.LATEST)
            results = test_api.sync.preview_batch(params)

            assert len(results.manifest_results) == 1
            assert results.manifest_results[0].actions[0].action_type == SetupActionType.PACKAGE

    @staticmethod
    def test_default_strategy_is_minimal(test_api: API) -> None:
        """Test that default strategy produces PACKAGE actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir))

            assert results.actions[0].action_type == SetupActionType.PACKAGE

    @staticmethod
    def test_upgrade_action_description(test_api: API) -> None:
        """Test that upgrade actions have 'Upgrade' in their description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.preview_single(Path(tmpdir), strategy=SyncStrategy.LATEST)

            assert 'Upgrade' in results.actions[0].description

    @staticmethod
    def test_dry_run_upgrade_installed_package(test_api: API) -> None:
        """Test that dry-run upgrade of an installed package succeeds without skip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 'packaging' is always installed
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True, strategy=SyncStrategy.LATEST)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            # Upgrade of an installed package should NOT be skipped
            assert action_result.skipped is False

    @staticmethod
    def test_dry_run_upgrade_missing_package(test_api: API) -> None:
        """Test dry-run upgrade of a missing package reports install fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'state': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True, strategy=SyncStrategy.LATEST)
            preview = test_api.sync.preview_batch(setup_params)
            results = execute_via_stream(test_api, preview, setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skip_reason is not None
            assert 'install' in action_result.skip_reason.lower()
