"""Tests for plugin filtering on SetupParameters and plugin list kinds."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.sync import SyncCommands
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PluginKind
from porringer.schema import (
    SetupParameters,
)
from tests.conftest import execute_via_stream

# Constants
FIRST_ACTION = 0


@pytest.mark.mock_packages
class TestSetupParametersPlugins:
    """Tests for SetupParameters.plugins include-list filtering."""

    @staticmethod
    async def test_plugins_none_preserves_all_actions(session_api: API) -> None:
        """When plugins is None, all actions should be preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # parse_manifest returns the unfiltered view
            full_results = session_api.sync.parse_manifest(Path(tmpdir))
            full_count = len(full_results.actions)
            assert full_count > 0

            # run() with plugins=None also returns all actions
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins=None,
            )
            batch = await session_api.sync.run(params)
            assert batch.total_actions == full_count

    @staticmethod
    async def test_plugins_filter_by_installer(session_api: API) -> None:
        """When plugins is set, only actions with matching installer are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Get the full action plan to find out what installer was resolved
            full_results = session_api.sync.parse_manifest(Path(tmpdir))
            package_actions = [a for a in full_results.actions if a.kind == PluginKind.PACKAGE]
            assert len(package_actions) > 0
            resolved_installer = package_actions[FIRST_ACTION].installer
            assert resolved_installer is not None

            # Filter to only that installer — should keep package actions + post_sync commands
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins={resolved_installer},
            )
            batch = await session_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    # Should be either our installer or a post-sync command (installer=None)
                    assert action.installer == resolved_installer or action.installer is None

    @staticmethod
    async def test_plugins_nonexistent_name_filters_all_package_actions(session_api: API) -> None:
        """When plugins names a nonexistent plugin, all plugin-backed actions are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins={'nonexistent_plugin_xyz'},
            )
            batch = await session_api.sync.run(params)
            # Only post_sync commands (installer=None) should remain
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    assert action.installer is None

    @staticmethod
    async def test_plugins_filter_preserves_post_sync(session_api: API) -> None:
        """Post-sync commands (installer=None) are always preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello', 'echo world'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Filter to a fake plugin — only post_sync should survive
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins={'nonexistent_plugin_xyz'},
            )
            batch = await session_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                command_actions = [a for a in manifest_result.actions if a.command is not None]
                expected_command_count = 2
                assert len(command_actions) == expected_command_count

    @staticmethod
    async def test_plugins_filter_applies_in_execute_stream(session_api: API) -> None:
        """The plugins filter also applies when using execute_stream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Filter to nonexistent plugin — should yield no package actions
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins={'nonexistent_plugin_xyz'},
            )
            batch = await execute_via_stream(session_api, params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    assert action.installer is None


class TestListPluginsKinds:
    """Tests for plugin list kinds filtering."""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _skip_tool_version():
        """Bypass ``tool_version()`` subprocess calls — these tests only verify kind filtering."""
        with (
            patch('porringer.backend.resolver.ToolBasedPlugin.tool_version', return_value=None),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            yield

    @staticmethod
    async def test_kinds_none_returns_all(session_api: API) -> None:
        """When kinds is None, all plugins should be returned."""
        results = await session_api.plugin.list()
        assert len(results) > 0

    @staticmethod
    @pytest.mark.parametrize('kind', [PluginKind.PACKAGE, PluginKind.TOOL, PluginKind.PROJECT])
    async def test_kinds_filter_single(session_api: API, kind: PluginKind) -> None:
        """Filtering by a single kind should only return plugins of that kind."""
        results = await session_api.plugin.list(kinds=[kind])
        for result in results:
            assert result.kind == kind

    @staticmethod
    async def test_kinds_filter_multiple(session_api: API) -> None:
        """Filtering by multiple kinds should return plugins of all requested kinds."""
        results = await session_api.plugin.list(kinds=[PluginKind.PACKAGE, PluginKind.TOOL])
        for result in results:
            assert result.kind in {PluginKind.PACKAGE, PluginKind.TOOL}

    @staticmethod
    async def test_list_results_include_kind(session_api: API) -> None:
        """Every result should have a kind field populated."""
        results = await session_api.plugin.list()
        for result in results:
            assert isinstance(result.kind, PluginKind)


@pytest.mark.mock_packages
class TestParseManifest:
    """Tests for parse_manifest."""

    @staticmethod
    def test_parse_manifest_action_fields(session_api: API) -> None:
        """parse_manifest actions expose installer, kind, ecosystem, and package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            results = SyncCommands.parse_manifest(Path(tmpdir))
            action = results.actions[FIRST_ACTION]

            assert action.kind == PluginKind.PACKAGE
            assert action.ecosystem == 'python'
            assert action.installer is not None
            assert action.package is not None
            assert str(action.package) == 'requests'
