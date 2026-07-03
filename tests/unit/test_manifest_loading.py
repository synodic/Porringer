"""Helpers for test manifest loading.

Test manifest loading, preview, batch operations, metadata, and sync CLI.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from packaging.version import Version

from porringer.api import API
from porringer.backend.command.core.action_builder import build_actions, parse_manifest
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters
from porringer.schema import SetupManifest, SetupParameters
from porringer.test.mock.project_environment import MockProjectEnvironment
from porringer.utility.exception import ManifestError

# Action indices
FIRST_ACTION_INDEX = 0
SECOND_ACTION_INDEX = 1
THIRD_ACTION_INDEX = 2

# Counts
EXPECTED_ACTIONS_JSON_MANIFEST = 1
SINGLE_MANIFEST = 1
DUAL_MANIFESTS = 2
THREE_ACTIONS = 3
TWO_ACTIONS = 2
SINGLE_FAILED_PATH = 1
NO_FAILED_PATHS = 0


class _AvailableMockProjectEnvironment(MockProjectEnvironment):
    """Mock project environment that is available for resolver tests."""

    @classmethod
    def is_available(cls) -> bool:
        return True


class _OtherProjectEnvironment(_AvailableMockProjectEnvironment):
    """Second mock project environment for the same ecosystem."""

    @classmethod
    def tool_name(cls) -> str:
        return 'other-project'


class _EvidenceProjectEnvironment(_OtherProjectEnvironment):
    """Project environment identified by a manager-specific lock file."""

    _project_evidence_files = ('other-project.lock',)


@pytest.mark.mock_packages
class TestSetupManifest:
    """Tests for manifest loading."""

    @staticmethod
    def test_load_json_manifest(session_api: API) -> None:
        """Test loading a porringer.json manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir))

            assert results.manifest_path == manifest_path.resolve()
            assert len(results.actions) == EXPECTED_ACTIONS_JSON_MANIFEST

    @staticmethod
    def test_load_pyproject_manifest(session_api: API) -> None:
        """Test loading from pyproject.toml [tool.porringer]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject_path = Path(tmpdir) / 'pyproject.toml'
            pyproject_content = """
[tool.porringer]
version = "1"
packages.python = ["requests"]
"""
            pyproject_path.write_text(pyproject_content)

            results = parse_manifest(Path(tmpdir))

            assert results.manifest_path == pyproject_path.resolve()
            assert any(action.kind == PluginKind.PACKAGE for action in results.actions)

    @staticmethod
    def test_missing_manifest_raises_error(session_api: API) -> None:
        """Test that missing manifest raises ManifestError."""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            parse_manifest(Path(tmpdir))


@pytest.mark.mock_packages
class TestSetupPreview:
    """Tests for setup preview."""

    @staticmethod
    def test_preview_builds_actions(session_api: API) -> None:
        """Test that preview builds correct action types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'pydantic']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir))

            assert len(results.actions) == TWO_ACTIONS

            action_kinds = [a.kind for a in results.actions]
            assert action_kinds[FIRST_ACTION_INDEX] == PluginKind.PACKAGE
            assert action_kinds[SECOND_ACTION_INDEX] == PluginKind.PACKAGE

    @staticmethod
    def test_preview_excludes_filtered_packages(session_api: API) -> None:
        """Test that packages with non-matching platforms are excluded from actions."""
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

            results = parse_manifest(Path(tmpdir))

            # Only 'requests' (no filter) and 'uvloop' (matching) should be included
            assert len(results.actions) == TWO_ACTIONS
            package_names = [str(a.package) for a in results.actions]
            assert 'requests' in package_names
            assert 'uvloop' in package_names
            assert 'pywin32' not in package_names

    @staticmethod
    def test_preview_includes_all_when_no_platform_filters(session_api: API) -> None:
        """Test that all packages are included when none have platform filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask', 'pytest']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir))

            assert len(results.actions) == THREE_ACTIONS

    @staticmethod
    def test_build_actions_adds_implicit_project_sync_for_relevant_plugins() -> None:
        """Relevant project plugins should add project-sync actions without manifest entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')

            plugin = _AvailableMockProjectEnvironment(
                PluginParameters(distribution=Distribution(version=Version('0.0.0')))
            )
            plugins = DiscoveredPlugins(
                environments={},
                project_environments={'mock-project': plugin},
                scm_environments={},
            )

            manifest = SetupManifest(version='1')
            actions = build_actions(manifest, plugins, search_from=project_root)

            assert any(action.kind == PluginKind.PROJECT and action.installer == 'mock-project' for action in actions)

    @staticmethod
    def test_build_actions_uses_preferred_project_plugin_when_multiple_are_relevant() -> None:
        """Preferences select one project-sync owner when markers match multiple plugins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
            params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
            plugins = DiscoveredPlugins(
                environments={},
                project_environments={
                    'mock-project': _AvailableMockProjectEnvironment(params),
                    'other-project': _OtherProjectEnvironment(params),
                },
                scm_environments={},
            )

            manifest = SetupManifest(version='1', preferences={Ecosystem('python'): 'other-project'})
            actions = build_actions(manifest, plugins, search_from=project_root)

            project_actions = [action for action in actions if action.kind == PluginKind.PROJECT]
            assert len(project_actions) == 1
            assert project_actions[0].installer == 'other-project'

    @staticmethod
    def test_build_actions_uses_project_evidence_when_multiple_are_relevant() -> None:
        """Manager-specific files select one project-sync owner without a preference."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
            (project_root / 'other-project.lock').touch()
            params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
            plugins = DiscoveredPlugins(
                environments={},
                project_environments={
                    'mock-project': _AvailableMockProjectEnvironment(params),
                    'other-project': _EvidenceProjectEnvironment(params),
                },
                scm_environments={},
            )

            actions = build_actions(SetupManifest(version='1'), plugins, search_from=project_root)

            project_actions = [action for action in actions if action.kind == PluginKind.PROJECT]
            assert len(project_actions) == 1
            assert project_actions[0].installer == 'other-project'

    @staticmethod
    def test_build_actions_skips_ambiguous_project_plugins_without_evidence() -> None:
        """Ambiguous marker-only project plugins do not all run implicitly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
            params = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
            plugins = DiscoveredPlugins(
                environments={},
                project_environments={
                    'mock-project': _AvailableMockProjectEnvironment(params),
                    'other-project': _OtherProjectEnvironment(params),
                },
                scm_environments={},
            )

            actions = build_actions(SetupManifest(version='1'), plugins, search_from=project_root)

            assert [action for action in actions if action.kind == PluginKind.PROJECT] == []


@pytest.mark.mock_packages
class TestSetupBatch:
    """Tests for batch setup operations."""

    @staticmethod
    async def test_preview_batch_single_path(session_api: API) -> None:
        """Test batch preview with a single path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.run(params)
            results = report.results

            assert len(results.manifest_results) == 1
            assert results.total_actions == 1
            assert len(results.failed_paths) == 0

    @staticmethod
    async def test_preview_batch_multiple_paths(session_api: API) -> None:
        """Test batch preview with multiple paths."""
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
            report = await session_api.sync.run(params)
            results = report.results

            assert len(results.manifest_results) == DUAL_MANIFESTS
            assert results.total_actions == THREE_ACTIONS  # 1 + 2
            assert len(results.failed_paths) == NO_FAILED_PATHS

    @staticmethod
    async def test_preview_batch_with_failures(session_api: API) -> None:
        """Test batch preview continues on manifest errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project1 = Path(tmpdir) / 'project1'
            project2 = Path(tmpdir) / 'project2'
            project1.mkdir()
            project2.mkdir()

            # Only project1 has a manifest
            (project1 / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            params = SetupParameters(paths=[project1, project2], fail_fast=False)
            report = await session_api.sync.run(params)
            results = report.results

            assert len(results.manifest_results) == SINGLE_MANIFEST
            assert len(results.failed_paths) == SINGLE_FAILED_PATH


@pytest.mark.mock_packages
class TestManifestMetadata:
    """Tests for manifest display metadata fields."""

    @staticmethod
    def test_metadata_in_json_manifest(session_api: API) -> None:
        """Test that display metadata is loaded from JSON and propagated to SetupResults."""
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

            results = parse_manifest(Path(tmpdir))

            assert results.metadata is not None
            assert results.metadata.name == 'Dev Environment'
            assert results.metadata.description == 'Tools for development'
            assert results.metadata.author == 'Synodic'
            assert results.metadata.url == 'https://example.com/'

    @staticmethod
    def test_metadata_none_when_not_provided(session_api: API) -> None:
        """Test that metadata fields are None when not in manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir))

            assert results.metadata is not None
            assert results.metadata.name is None
            assert results.metadata.description is None
            assert results.metadata.author is None
            assert results.metadata.url is None

    @staticmethod
    def test_package_description_in_actions(session_api: API) -> None:
        """Test that per-package descriptions propagate to SetupAction."""
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

            results = parse_manifest(Path(tmpdir))

            assert len(results.actions) == TWO_ACTIONS
            assert str(results.actions[0].package) == 'ruff'
            assert results.actions[0].package_description == 'Fast linter'
            assert str(results.actions[1].package) == 'pytest'
            assert results.actions[1].package_description is None


@pytest.mark.mock_packages
class TestSetupCLI:
    """Tests for setup CLI commands (now via sync)."""

    @staticmethod
    async def test_sync_inspect_api(session_api: API) -> None:
        """Test the sync inspection functionality via API."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.inspect(setup_params)

            # Should have 1 action for pip install
            assert len(report.manifests) == 1
            assert len(report.manifests[0].actions) == 1
            assert report.manifests[0].actions[0].success
