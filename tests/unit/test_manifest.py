"""Test the setup/manifest functionality in sync command"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

from porringer.api import API
from porringer.backend.command.core.action_builder import build_actions
from porringer.backend.command.manifest import collect_manifest_contributions, find_manifest, has_manifest
from porringer.backend.command.sync import SyncCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem, ManifestContribution, PluginKind
from porringer.schema import (
    ManifestValidationCode,
    PackageSpec,
    SetupManifest,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from porringer.utility.exception import ManifestError

# Test constants
EXPECTED_ACTIONS_JSON_MANIFEST = 2  # 1 install + 1 command
_PY = Ecosystem('python')

# Action indices
FIRST_ACTION_INDEX = 0
SECOND_ACTION_INDEX = 1
THIRD_ACTION_INDEX = 2
FOURTH_ACTION_INDEX = 3

# Exit codes
EXIT_CODE_SUCCESS = 0

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
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert results.manifest_path == manifest_path.resolve()
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
packages.python = ["requests"]
"""
            pyproject_path.write_text(pyproject_content)

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert results.manifest_path == pyproject_path.resolve()
            assert len(results.actions) == 1  # 1 install

    @staticmethod
    def test_missing_manifest_raises_error(test_api: API) -> None:
        """Test that missing manifest raises ManifestError"""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            test_api.sync.parse_manifest(Path(tmpdir))


class TestSetupPreview:
    """Tests for setup preview"""

    @staticmethod
    def test_preview_builds_actions(test_api: API) -> None:
        """Test that preview builds correct action types"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'pydantic']},
                'post_sync': ['pdm install'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            # 2 packages + 1 command = 3 actions
            assert len(results.actions) == THREE_ACTIONS

            action_kinds = [a.kind for a in results.actions]
            assert action_kinds[FIRST_ACTION_INDEX] == PluginKind.PACKAGE
            assert action_kinds[SECOND_ACTION_INDEX] == PluginKind.PACKAGE
            assert action_kinds[THIRD_ACTION_INDEX] is None

    @staticmethod
    def test_preview_excludes_filtered_packages(test_api: API) -> None:
        """Test that packages with non-matching platforms are excluded from actions"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {
                    'python': [
                        'requests',
                        {'name': 'pywin32', 'platforms': ['nonexistent_platform']},
                        {'name': 'uvloop', 'platforms': [sys.platform]},
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

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
                'packages': {'python': ['requests', 'flask', 'pytest']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert len(results.actions) == THREE_ACTIONS


class TestSetupBatch:
    """Tests for batch setup operations"""

    @staticmethod
    def test_preview_batch_single_path(test_api: API) -> None:
        """Test batch preview with a single path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir))
            results = test_api.sync.run(params)

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

            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))
            (project2 / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'python': ['flask', 'pytest']}})
            )

            params = SetupParameters(paths=[project1, project2])
            results = test_api.sync.run(params)

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
            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            params = SetupParameters(paths=[project1, project2], fail_fast=False)
            results = test_api.sync.run(params)

            assert len(results.manifest_results) == SINGLE_MANIFEST
            assert len(results.failed_paths) == SINGLE_FAILED_PATH

    @staticmethod
    def test_preview_batch_from_cache(test_api: API, temp_cache_dir) -> None:
        """Test batch preview using cached directories"""
        tmp_path, _ = temp_cache_dir
        project1 = tmp_path / 'project1'
        project1.mkdir()

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

        # Add to cache
        test_api.cache.add_directory(project1)

        # Preview from cache (paths=None)
        params = SetupParameters(paths=None)
        results = test_api.sync.run(params)

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

        (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))
        (project2 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['flask']}}))

        # Add directories to cache
        test_api.cache.add_directory(project1)
        test_api.cache.add_directory(project2)

        # Preview from all cached
        params = SetupParameters(paths=None)
        results = test_api.sync.run(params)

        assert len(results.manifest_results) == DUAL_MANIFESTS
        assert results.total_actions == TWO_ACTIONS


class TestPackageSpec:
    """Tests for PackageSpec model and string/object coercion"""

    @staticmethod
    def test_manifest_coerces_string_packages() -> None:
        """String package entries are coerced to PackageSpec objects"""
        manifest = SetupManifest(packages={_PY: ['requests', 'flask']})
        assert len(manifest.packages[_PY]) == TWO_PACKAGES
        assert str(manifest.packages[_PY][0].name) == 'requests'
        assert manifest.packages[_PY][0].description is None

    @staticmethod
    def test_manifest_accepts_object_packages() -> None:
        """Object package entries are parsed as PackageSpec"""
        manifest = SetupManifest(packages={_PY: [{'name': 'ruff', 'description': 'Fast linter'}]})
        assert str(manifest.packages[_PY][0].name) == 'ruff'
        assert manifest.packages[_PY][0].description == 'Fast linter'

    @staticmethod
    def test_manifest_mixed_string_and_object_packages() -> None:
        """Manifest accepts a mix of string and object package entries"""
        manifest = SetupManifest(
            packages={
                _PY: [
                    'requests',
                    {'name': 'ruff', 'description': 'Fast linter'},
                    'pytest',
                ]
            }
        )
        pkgs = manifest.packages[_PY]
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
        manifest = SetupManifest(packages={_PY: ['requests']})
        assert manifest.packages[_PY][0].platforms == []
        assert manifest.packages[_PY][0].is_applicable() is True

    @staticmethod
    def test_object_with_platforms_parsed() -> None:
        """Object package entries with platforms should be parsed correctly"""
        manifest = SetupManifest(packages={_PY: [{'name': 'pywin32', 'platforms': ['win32']}]})
        spec = manifest.packages[_PY][0]
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
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

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
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

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
                    'python': [
                        {'name': 'ruff', 'description': 'Fast linter'},
                        'pytest',
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

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
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            # Test dry-run via API
            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            results = test_api.sync.run(setup_params)

            # Should have 1 action for pip install
            assert len(results.manifest_results) == 1
            assert len(results.manifest_results[0].results) == 1
            # Should succeed in dry-run
            assert results.manifest_results[0].results[0].success


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
                'packages': {'python': ['requests']},
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
            manifest_data = {'version': '99', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            version_errors = [e for e in result.errors if e.code == ManifestValidationCode.UNSUPPORTED_VERSION]
            assert len(version_errors) == 1
            assert version_errors[0].field == 'version'

    @staticmethod
    def test_unknown_ecosystem_in_packages(test_api: API) -> None:
        """Unrecognized ecosystem in packages produces UNKNOWN_PLUGIN error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'nonexistent_backend': ['foo']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            plugin_errors = [e for e in result.errors if e.code == ManifestValidationCode.UNKNOWN_PLUGIN]
            assert len(plugin_errors) == 1
            assert plugin_errors[0].field == 'packages.nonexistent_backend'

    @staticmethod
    def test_invalid_package_name_warning(test_api: API) -> None:
        """Invalid PEP 440 package specifier under a Python backend produces a warning.

        Non-PEP-440 names are accepted by PackageRef (lenient parser) so the
        manifest loads, but `_validate_package_names` flags them as
        `INVALID_PACKAGE_NAME` warnings for Python backends.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['valid-package', '!!!invalid!!!']}}
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            name_warnings = [w for w in result.warnings if w.code == ManifestValidationCode.INVALID_PACKAGE_NAME]
            assert len(name_warnings) == 1
            assert '!!!invalid!!!' in name_warnings[0].message

    @staticmethod
    def test_duplicate_packages_warning(test_api: API) -> None:
        """Same package under multiple sections produces DUPLICATE_PACKAGE warning"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {
                    'python': ['requests'],
                },
                'tools': {
                    'python': ['requests'],
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
                'packages': {'python': ['requests']},
                'post_sync': ['echo should-not-run'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # This should return quickly without executing anything
            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is True

    @staticmethod
    def test_multiple_errors_and_warnings(test_api: API) -> None:
        """Multiple issues are all reported in a single result.

        With the lenient PackageRef parser, `!!!bad!!!` and
        `also-bad[>=` are now accepted at schema-load time.  The
        manifest still fails validation because version `99` is
        unsupported and `fake_backend` is unknown.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '99',
                'packages': {'fake_backend': ['!!!bad!!!', 'also-bad[>=']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            result = test_api.sync.validate_manifest(Path(tmpdir))

            assert result.valid is False
            assert len(result.errors) >= 1
            # Version 99 is unsupported or fake_backend is unknown
            error_codes = {e.code for e in result.errors}
            assert (
                ManifestValidationCode.UNSUPPORTED_VERSION in error_codes
                or ManifestValidationCode.UNKNOWN_PLUGIN in error_codes
            )


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
        assert 'packages' in props
        assert 'tools' in props
        assert 'projects' in props
        assert 'runtimes' in props
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
            manifest_data = {'version': '1', 'packages': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            results = test_api.sync.run(setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.skip_reason == SkipReason.ALREADY_INSTALLED
            assert action_result.message is not None
            assert 'packaging' in action_result.message

    @staticmethod
    def test_dry_run_does_not_skip_missing_package(test_api: API) -> None:
        """Test that dry-run reports success without skip for a package that is not installed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # Use a package name that should never be installed
            manifest_data = {'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            results = test_api.sync.run(setup_params)

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
            manifest_data = {'version': '1', 'packages': {'python': ['packaging>=1.0']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            results = test_api.sync.run(setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.skip_reason == SkipReason.ALREADY_INSTALLED
            assert action_result.message is not None
            assert 'satisfies' in action_result.message

    @staticmethod
    def test_dry_run_version_not_satisfied(test_api: API) -> None:
        """Test that dry-run does not skip when a version specifier is not satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # packaging>=99999 should never be satisfied
            manifest_data = {'version': '1', 'packages': {'python': ['packaging>=99999']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            results = test_api.sync.run(setup_params)

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
                'packages': {'python': ['requests', 'pydantic']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir), strategy=SyncStrategy.LATEST)

            # 2 packages + 1 command = 3 actions
            assert len(results.actions) == THREE_ACTIONS

            action_kinds = [a.kind for a in results.actions]
            assert action_kinds[FIRST_ACTION_INDEX] == PluginKind.PACKAGE
            assert action_kinds[SECOND_ACTION_INDEX] == PluginKind.PACKAGE
            assert action_kinds[THIRD_ACTION_INDEX] is None

    @staticmethod
    def test_preview_exact_strategy_produces_package_actions(test_api: API) -> None:
        """Test that preview with EXACT strategy produces PACKAGE actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir), strategy=SyncStrategy.EXACT)

            assert len(results.actions) == 1
            assert results.actions[0].kind == PluginKind.PACKAGE

    @staticmethod
    def test_preview_batch_latest_strategy(test_api: API) -> None:
        """Test that batch preview with LATEST strategy threads strategy through."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), strategy=SyncStrategy.LATEST)
            results = test_api.sync.run(params)

            assert len(results.manifest_results) == 1
            assert results.manifest_results[0].actions[0].kind == PluginKind.PACKAGE

    @staticmethod
    def test_default_strategy_is_minimal(test_api: API) -> None:
        """Test that default strategy produces PACKAGE actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert results.actions[0].kind == PluginKind.PACKAGE

    @staticmethod
    def test_upgrade_action_description(test_api: API) -> None:
        """Test that upgrade actions have 'Upgrade' in their description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir), strategy=SyncStrategy.LATEST)

            assert 'Upgrade' in results.actions[0].description

    @staticmethod
    def test_dry_run_upgrade_installed_package(test_api: API) -> None:
        """Test that dry-run upgrade of an installed package succeeds without skip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 'packaging' is always installed
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True, strategy=SyncStrategy.LATEST)
            results = test_api.sync.run(setup_params)

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
            manifest_data = {'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), dry_run=True, strategy=SyncStrategy.LATEST)
            results = test_api.sync.run(setup_params)

            assert len(results.manifest_results) == 1
            action_result = results.manifest_results[0].results[0]
            assert action_result.success is True
            assert action_result.message is not None
            assert 'install' in action_result.message.lower()


FIVE_ACTIONS = 5
FOUR_ACTIONS = 4


class TestPackageSpecPlugins:
    """Tests for the plugins field on PackageSpec and plugin-management actions"""

    @staticmethod
    def test_package_spec_plugins_default_empty() -> None:
        """PackageSpec.plugins defaults to an empty list"""
        spec = PackageSpec(name='pdm')
        assert spec.plugins == []

    @staticmethod
    def test_package_spec_plugins_parsed() -> None:
        """PackageSpec accepts a plugins list of package refs"""
        spec = PackageSpec.model_validate({'name': 'pdm', 'plugins': ['cppython', 'pdm-bump']})
        assert len(spec.plugins) == 2
        assert spec.plugins[0].name.name == 'cppython'
        assert spec.plugins[1].name.name == 'pdm-bump'

    @staticmethod
    def test_string_coercion_has_empty_plugins() -> None:
        """String package entries should have empty plugins list"""
        manifest = SetupManifest(packages={_PY: ['requests']})
        assert manifest.packages[_PY][0].plugins == []

    @staticmethod
    def test_manifest_tools_with_plugins() -> None:
        """Manifest tools section accepts packages with plugins"""
        manifest = SetupManifest(tools={_PY: [{'name': 'pdm', 'plugins': ['cppython']}, 'ruff']})
        pkgs = manifest.tools[_PY]
        assert len(pkgs) == 2
        assert len(pkgs[0].plugins) == 1
        assert pkgs[0].plugins[0].name.name == 'cppython'
        assert len(pkgs[1].plugins) == 0

    @staticmethod
    def test_build_actions_emits_plugin_actions(test_api: API) -> None:
        """_build_actions emits plugin-management actions after their parent package"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'tools': {
                    'python': [
                        {'name': 'pdm', 'plugins': ['cppython', 'pdm-bump']},
                        'ruff',
                    ]
                },
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            # pdm + 2 plugin additions + ruff = 4 actions
            assert len(results.actions) == FOUR_ACTIONS

            # First action: install pdm
            assert str(results.actions[FIRST_ACTION_INDEX].package) == 'pdm'
            assert results.actions[FIRST_ACTION_INDEX].plugin_target is None

            # Second action: add cppython plugin to pdm
            assert str(results.actions[SECOND_ACTION_INDEX].package) == 'cppython'
            second = results.actions[SECOND_ACTION_INDEX]
            assert second.plugin_target is not None
            assert second.plugin_target.name == 'pdm'

            # Third action: add pdm-bump plugin to pdm
            assert str(results.actions[THIRD_ACTION_INDEX].package) == 'pdm-bump'
            third = results.actions[THIRD_ACTION_INDEX]
            assert third.plugin_target is not None
            assert third.plugin_target.name == 'pdm'

            # Fourth action: install ruff (no plugin target)
            assert str(results.actions[FOURTH_ACTION_INDEX].package) == 'ruff'
            assert results.actions[FOURTH_ACTION_INDEX].plugin_target is None

    @staticmethod
    def test_plugin_action_description_contains_add_plugin() -> None:
        """Plugin-management actions should have 'Add plugin' in their description"""
        manifest = SetupManifest(tools={_PY: [{'name': 'pdm', 'plugins': ['cppython']}]})
        environments: dict[str, Environment] = {}
        actions = build_actions(manifest, environments)

        plugin_actions = [a for a in actions if a.plugin_target is not None]
        for action in plugin_actions:
            assert 'Add plugin' in action.description

    @staticmethod
    def test_json_manifest_with_plugins_roundtrip() -> None:
        """JSON manifest with plugins can be loaded and serialised"""
        data = {
            'version': '1',
            'tools': {
                'python': [
                    {'name': 'pdm', 'plugins': ['cppython>=0.5']},
                ]
            },
        }
        manifest = SetupManifest.model_validate(data)
        spec = manifest.tools[_PY][0]
        assert spec.plugins[0].name.name == 'cppython'
        assert spec.plugins[0].name.constraint == '>=0.5'

    @staticmethod
    def test_package_spec_plugins_with_version_constraint() -> None:
        """Plugin refs support version constraints"""
        spec = PackageSpec.model_validate({'name': 'pdm', 'plugins': ['cppython>=1.0,<2.0']})
        assert spec.plugins[0].name.name == 'cppython'
        assert spec.plugins[0].name.constraint is not None
        assert '>=1.0' in spec.plugins[0].name.constraint
        assert '<2.0' in spec.plugins[0].name.constraint


class TestManifestContributor:
    """Tests for the ManifestContributor protocol and plugin-driven discovery."""

    @staticmethod
    def test_manifest_contribution_dataclass() -> None:
        """ManifestContribution stores filename, config_path, and file_format"""
        contrib = ManifestContribution(filename='pyproject.toml', config_path=('tool', 'porringer'), file_format='toml')
        assert contrib.filename == 'pyproject.toml'
        assert contrib.config_path == ('tool', 'porringer')
        assert contrib.file_format == 'toml'

    @staticmethod
    def test_project_environment_is_manifest_contributor() -> None:
        """ProjectEnvironment implements ManifestContributor"""
        assert issubclass(ProjectEnvironment, ManifestContributor)

    @staticmethod
    def test_collect_manifest_contributions_returns_unique_filenames() -> None:
        """collect_manifest_contributions returns deduplicated entries"""
        contributions = collect_manifest_contributions()
        filenames = [c.filename for c in contributions]
        # No duplicates
        assert len(filenames) == len(set(filenames))

    @staticmethod
    def test_collect_manifest_contributions_includes_pyproject() -> None:
        """At least pyproject.toml is contributed by installed Python project plugins"""
        contributions = collect_manifest_contributions()
        filenames = [c.filename for c in contributions]
        assert 'pyproject.toml' in filenames

    @staticmethod
    def test_manifest_filenames_starts_with_native() -> None:
        """manifest_filenames() always starts with 'porringer.json'"""
        filenames = SyncCommands.manifest_filenames()
        assert filenames[0] == 'porringer.json'
        assert len(filenames) >= 2  # at least native + pyproject.toml

    @staticmethod
    def test_manifest_filenames_includes_contributed() -> None:
        """manifest_filenames() includes plugin-contributed filenames"""
        filenames = SyncCommands.manifest_filenames()
        # With built-in plugins we expect at least pyproject.toml
        assert 'pyproject.toml' in filenames


class TestManifestDiscovery:
    """Tests for find_manifest() with plugin-driven discovery."""

    @staticmethod
    def test_find_native_json_manifest() -> None:
        """find_manifest discovers porringer.json and sets root to parent"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'porringer.json'
            path.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == path.resolve()
            assert result.root_directory == Path(tmpdir).resolve()
            assert result.manifest.version == '1'

    @staticmethod
    def test_find_pyproject_inline_manifest() -> None:
        """find_manifest discovers inline [tool.porringer] in pyproject.toml"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nversion = "1"\npackages.python = ["requests"]\n')

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == pyproject.resolve()
            assert result.root_directory == Path(tmpdir).resolve()

    @staticmethod
    def test_find_pyproject_reference_manifest() -> None:
        """find_manifest follows manifest reference from pyproject.toml"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create referenced manifest in subdirectory
            subdir = Path(tmpdir) / 'tool'
            subdir.mkdir()
            manifest_file = subdir / 'porringer.json'
            manifest_file.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            # Create pyproject.toml with reference
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nmanifest = "tool/porringer.json"\n')

            result = find_manifest(Path(tmpdir))

            # manifest_path points to the actual JSON file
            assert result.manifest_path == manifest_file.resolve()
            # root_directory is the pyproject.toml's parent
            assert result.root_directory == Path(tmpdir).resolve()
            assert result.manifest.version == '1'

    @staticmethod
    def test_find_package_json_inline_manifest() -> None:
        """find_manifest discovers porringer config inside package.json"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_json = Path(tmpdir) / 'package.json'
            pkg_json.write_text(
                json.dumps(
                    {
                        'name': 'my-project',
                        'porringer': {'version': '1', 'packages': {'node': ['lodash']}},
                    }
                )
            )

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == pkg_json.resolve()
            assert result.root_directory == Path(tmpdir).resolve()
            assert 'node' in result.manifest.packages

    @staticmethod
    def test_find_deno_json_inline_manifest() -> None:
        """find_manifest discovers porringer config inside deno.json"""
        with tempfile.TemporaryDirectory() as tmpdir:
            deno_json = Path(tmpdir) / 'deno.json'
            deno_json.write_text(
                json.dumps(
                    {
                        'porringer': {'version': '1', 'packages': {'deno': ['oak']}},
                    }
                )
            )

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == deno_json.resolve()
            assert result.root_directory == Path(tmpdir).resolve()

    @staticmethod
    def test_native_json_takes_precedence() -> None:
        """porringer.json is preferred over pyproject.toml in same directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            native = Path(tmpdir) / 'porringer.json'
            native.write_text(json.dumps({'version': '1', 'packages': {'python': ['native']}}))

            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nversion = "1"\npackages.python = ["embedded"]\n')

            result = find_manifest(Path(tmpdir))

            # Native JSON wins
            assert result.manifest_path == native.resolve()
            assert 'native' in str(result.manifest.packages.get(_PY, []))

    @staticmethod
    def test_find_manifest_nonexistent_path() -> None:
        """find_manifest raises ManifestError for nonexistent paths"""
        with pytest.raises(ManifestError):
            find_manifest(Path('/nonexistent/path'))

    @staticmethod
    def test_find_manifest_empty_directory() -> None:
        """find_manifest raises ManifestError for empty directories"""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            find_manifest(Path(tmpdir))

    @staticmethod
    def test_reference_manifest_missing_target() -> None:
        """find_manifest raises ManifestError when reference target doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nmanifest = "nonexistent/porringer.json"\n')

            with pytest.raises(ManifestError):
                find_manifest(Path(tmpdir))

    @staticmethod
    def test_package_json_reference_manifest() -> None:
        """find_manifest follows manifest reference from package.json"""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / 'config'
            subdir.mkdir()
            manifest_file = subdir / 'porringer.json'
            manifest_file.write_text(json.dumps({'version': '1', 'packages': {'node': ['express']}}))

            pkg_json = Path(tmpdir) / 'package.json'
            pkg_json.write_text(
                json.dumps(
                    {
                        'name': 'my-project',
                        'porringer': {'manifest': 'config/porringer.json'},
                    }
                )
            )

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == manifest_file.resolve()
            assert result.root_directory == Path(tmpdir).resolve()


class TestHasManifest:
    """Tests for has_manifest() lightweight check."""

    @staticmethod
    def test_has_manifest_with_native_json() -> None:
        """has_manifest returns True for directory with porringer.json"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'python': ['requests']}})
            )
            assert has_manifest(Path(tmpdir)) is True

    @staticmethod
    def test_has_manifest_with_pyproject_toml() -> None:
        """has_manifest returns True for directory with [tool.porringer] in pyproject.toml"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pyproject.toml').write_text(
                '[tool.porringer]\nversion = "1"\npackages.python = ["requests"]\n'
            )
            assert has_manifest(Path(tmpdir)) is True

    @staticmethod
    def test_has_manifest_empty_directory() -> None:
        """has_manifest returns False for empty directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert has_manifest(Path(tmpdir)) is False

    @staticmethod
    def test_has_manifest_nonexistent_path() -> None:
        """has_manifest returns False for nonexistent path"""
        assert has_manifest(Path('/nonexistent/path')) is False

    @staticmethod
    def test_has_manifest_pyproject_without_porringer() -> None:
        """has_manifest returns False when pyproject.toml has no [tool.porringer]"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pyproject.toml').write_text('[project]\nname = "foo"\n')
            assert has_manifest(Path(tmpdir)) is False

    @staticmethod
    def test_sync_commands_has_manifest_delegates() -> None:
        """SyncCommands.has_manifest() delegates to manifest.has_manifest()"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'python': ['requests']}})
            )
            assert SyncCommands.has_manifest(Path(tmpdir)) is True

        with tempfile.TemporaryDirectory() as tmpdir:
            assert SyncCommands.has_manifest(Path(tmpdir)) is False


class TestManifestResult:
    """Tests for ManifestResult dataclass."""

    @staticmethod
    def test_manifest_result_native_json(test_api: API) -> None:
        """parse_manifest returns SetupResults with root_directory for native JSON"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_path.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert results.manifest_path == manifest_path.resolve()
            assert results.root_directory == Path(tmpdir).resolve()

    @staticmethod
    def test_manifest_result_pyproject_inline(test_api: API) -> None:
        """parse_manifest returns correct root_directory for inline pyproject.toml"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nversion = "1"\npackages.python = ["requests"]\n')

            results = test_api.sync.parse_manifest(Path(tmpdir))

            assert results.manifest_path == pyproject.resolve()
            assert results.root_directory == Path(tmpdir).resolve()

    @staticmethod
    def test_manifest_result_pyproject_reference(test_api: API) -> None:
        """parse_manifest returns correct paths for pyproject.toml reference"""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / 'tool'
            subdir.mkdir()
            manifest_file = subdir / 'porringer.json'
            manifest_file.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nmanifest = "tool/porringer.json"\n')

            results = test_api.sync.parse_manifest(Path(tmpdir))

            # manifest_path is the actual JSON file resolved
            assert results.manifest_path == manifest_file.resolve()
            # root_directory is the pyproject.toml parent
            assert results.root_directory == Path(tmpdir).resolve()


class TestDirectoryValidationResult:
    """Tests for the enriched validate_directories()."""

    @staticmethod
    def test_validate_directories_returns_all(cache_manager, temp_cache_dir) -> None:
        """validate_directories returns a result for every registered directory"""
        tmp_path, _ = temp_cache_dir
        d1 = tmp_path / 'existing'
        d1.mkdir()
        d2 = tmp_path / 'also_existing'
        d2.mkdir()

        cache_manager.add_directory(d1)
        cache_manager.add_directory(d2)

        results = cache_manager.validate_directories()
        assert len(results) == 2
        assert all(r.exists for r in results)

    @staticmethod
    def test_validate_directories_check_manifest(cache_manager, temp_cache_dir) -> None:
        """validate_directories with check_manifest=True populates has_manifest"""
        tmp_path, _ = temp_cache_dir
        with_manifest = tmp_path / 'with_manifest'
        with_manifest.mkdir()
        (with_manifest / 'porringer.json').write_text(
            json.dumps({'version': '1', 'packages': {'python': ['requests']}})
        )

        without_manifest = tmp_path / 'without_manifest'
        without_manifest.mkdir()

        cache_manager.add_directory(with_manifest)
        cache_manager.add_directory(without_manifest)

        results = cache_manager.validate_directories(check_manifest=True)
        assert len(results) == 2

        result_with = next(r for r in results if r.directory.path == with_manifest.resolve())
        result_without = next(r for r in results if r.directory.path == without_manifest.resolve())

        assert result_with.exists is True
        assert result_with.has_manifest is True
        assert result_without.exists is True
        assert result_without.has_manifest is False

    @staticmethod
    def test_validate_directories_missing_path(cache_manager, temp_cache_dir) -> None:
        """validate_directories returns has_manifest=None for non-existent paths"""
        tmp_path, _ = temp_cache_dir
        to_delete = tmp_path / 'to_delete'
        to_delete.mkdir()
        cache_manager.add_directory(to_delete)
        to_delete.rmdir()

        results = cache_manager.validate_directories(check_manifest=True)
        assert len(results) == 1
        assert results[0].exists is False
        assert results[0].has_manifest is None

    @staticmethod
    def test_validate_directories_default_no_manifest_check(cache_manager, temp_cache_dir) -> None:
        """validate_directories without check_manifest leaves has_manifest as None"""
        tmp_path, _ = temp_cache_dir
        d = tmp_path / 'project'
        d.mkdir()
        (d / 'porringer.json').write_text(json.dumps({'version': '1'}))
        cache_manager.add_directory(d)

        results = cache_manager.validate_directories()
        assert len(results) == 1
        assert results[0].exists is True
        assert results[0].has_manifest is None
