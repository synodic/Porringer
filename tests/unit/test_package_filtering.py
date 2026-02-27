"""Tests for include_packages filtering on SetupParameters."""

import json
import tempfile
from pathlib import Path

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import SetupParameters
from tests.conftest import execute_via_stream

# Constants
FIRST_ACTION = 0


class TestIncludePackages:
    """Tests for SetupParameters.include_packages filtering."""

    @staticmethod
    def test_include_packages_none_preserves_all_actions(test_api: API) -> None:
        """When include_packages is None, all actions should be preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            full_results = test_api.sync.parse_manifest(Path(tmpdir))
            full_count = len(full_results.actions)
            assert full_count > 0

            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                include_packages=None,
            )
            batch = test_api.sync.run(params)
            assert batch.total_actions == full_count

    @staticmethod
    def test_include_packages_filters_by_name(test_api: API) -> None:
        """When include_packages is set, only matching package actions are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                include_packages={'requests'},
            )
            batch = test_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    # Should be 'requests' or a non-package action (command)
                    if action.package is not None:
                        assert action.package.name.lower() == 'requests'

    @staticmethod
    def test_include_packages_case_insensitive(test_api: API) -> None:
        """Package name matching should be case-insensitive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Use uppercase name — should still match
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                include_packages={'REQUESTS'},
            )
            batch = test_api.sync.run(params)
            package_actions = [
                a
                for m in batch.manifest_results
                for a in m.actions
                if a.package is not None
            ]
            assert len(package_actions) > 0
            for action in package_actions:
                assert action.package is not None
                assert action.package.name.lower() == 'requests'

    @staticmethod
    def test_include_packages_nonexistent_removes_all_package_actions(test_api: API) -> None:
        """When include_packages names a nonexistent package, all package actions are removed."""
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
                include_packages={'nonexistent_package_xyz'},
            )
            batch = test_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    assert action.package is None

    @staticmethod
    def test_include_packages_preserves_post_sync(test_api: API) -> None:
        """Post-sync commands (package=None) are always preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello', 'echo world'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                include_packages={'nonexistent_package_xyz'},
            )
            batch = test_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                command_actions = [a for a in manifest_result.actions if a.command is not None]
                expected_command_count = 2
                assert len(command_actions) == expected_command_count

    @staticmethod
    def test_include_packages_composes_with_plugins(test_api: API) -> None:
        """Both plugins and include_packages should compose (intersection)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask']},
                'post_sync': ['echo done'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Get the resolved installer name
            full_results = test_api.sync.parse_manifest(Path(tmpdir))
            package_actions = [a for a in full_results.actions if a.kind == PluginKind.PACKAGE]
            assert len(package_actions) > 0
            resolved_installer = package_actions[FIRST_ACTION].installer
            assert resolved_installer is not None

            # Filter by both plugin and package — only 'requests' via the resolved installer
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                plugins={resolved_installer},
                include_packages={'requests'},
            )
            batch = test_api.sync.run(params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    if action.package is not None:
                        assert action.package.name.lower() == 'requests'
                        assert action.installer == resolved_installer

    @staticmethod
    async def test_include_packages_applies_in_execute_stream(test_api: API) -> None:
        """The include_packages filter also applies when using execute_stream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests', 'flask']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            # Filter to only 'requests' — 'flask' actions should be excluded
            params = SetupParameters(
                paths=Path(tmpdir),
                dry_run=True,
                include_packages={'requests'},
            )
            batch = await execute_via_stream(test_api, params)
            for manifest_result in batch.manifest_results:
                for action in manifest_result.actions:
                    if action.package is not None:
                        assert action.package.name.lower() == 'requests'
