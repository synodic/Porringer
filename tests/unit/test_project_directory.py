"""Tests for project_directory and SkipReason functionality."""

import json
import tempfile
from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.schema import Ecosystem, PluginKind
from porringer.schema import (
    BatchSetupResults,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SkipReason,
)


@pytest.mark.mock_packages
class TestProjectDirectorySkip:
    """Tests for project_directory=False skipping PROJECT_SYNC and RUN_COMMAND actions."""

    @staticmethod
    async def test_false_skips_project_sync_not_post_sync(session_api: API) -> None:
        """Post-sync commands still execute when project_directory is False.

        Only PROJECT_SYNC actions are skipped — `post_sync` commands are
        independent of the project directory setting.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), project_directory=False, dry_run=True)
            results = await session_api.sync.run(params)

            # Should show all actions (1 package + 1 command)
            expected_action_count = 2
            assert results.total_actions == expected_action_count

            # The RUN_COMMAND should NOT be skipped — post_sync is decoupled
            command_skips = [r for r in results.skips if r.action.kind is None]
            assert len(command_skips) == 0

            # The RUN_COMMAND should succeed (dry-run always succeeds)
            command_results = [r for mr in results.manifest_results for r in mr.results if r.action.kind is None]
            assert len(command_results) == 1
            assert command_results[0].success is True

    @staticmethod
    async def test_false_keeps_package_actions(session_api: API) -> None:
        """Package actions still execute when project_directory is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), project_directory=False, dry_run=True)
            results = await session_api.sync.run(params)

            # Package action should not be skipped due to project_directory
            package_results = [
                r for mr in results.manifest_results for r in mr.results if r.action.kind == PluginKind.PACKAGE
            ]
            assert len(package_results) == 1
            project_skips = [
                r for r in package_results if r.skipped and r.skip_reason == SkipReason.NO_PROJECT_DIRECTORY
            ]
            assert len(project_skips) == 0

    @staticmethod
    async def test_none_runs_post_sync(session_api: API) -> None:
        """Post-sync commands execute normally when project_directory is None (default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            assert params.project_directory is None

            results = await session_api.sync.run(params)

            # The RUN_COMMAND should NOT be in skips
            command_skips = [r for r in results.skips if r.action.kind is None]
            assert len(command_skips) == 0


class TestBatchSetupResultsSkips:
    """Tests for BatchSetupResults.skips, total_skipped, and total_succeeded."""

    @staticmethod
    def _make_result(
        kind: PluginKind | None,
        skipped: bool = False,
        skip_reason: SkipReason | None = None,
        message: str | None = None,
    ) -> SetupActionResult:
        label = kind.name if kind is not None else 'COMMAND'
        action = SetupAction(
            description=f'Test {label}',
            kind=kind,
        )
        return SetupActionResult(
            action=action,
            success=True,
            skipped=skipped,
            skip_reason=skip_reason,
            message=message,
        )

    def test_skips_returns_only_skipped(self) -> None:
        """The skips property returns only skipped results."""
        r1 = self._make_result(PluginKind.PACKAGE, skipped=False)
        r2 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory provided',
        )
        r3 = self._make_result(None, skipped=False)

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2, r3])],
        )

        assert batch.total_skipped == 1
        assert len(batch.skips) == 1
        assert batch.skips[0].skipped
        assert batch.skips[0].skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_skips_empty_when_none_skipped(self) -> None:
        """The skips property returns empty list when nothing is skipped."""
        r1 = self._make_result(PluginKind.PACKAGE)
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1])],
        )

        assert batch.total_skipped == 0
        assert batch.skips == []

    @staticmethod
    def test_skips_carries_action_metadata() -> None:
        """Each skipped result carries full action metadata."""
        action = SetupAction(
            description='Sync project via uv',
            kind=PluginKind.PROJECT,
            ecosystem=Ecosystem('python'),
            installer='uv',
        )
        result = SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory provided',
        )
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[result])],
        )

        skip = batch.skips[0]
        assert skip.action.kind == PluginKind.PROJECT
        assert skip.action.ecosystem == 'python'
        assert skip.action.installer == 'uv'
        assert skip.skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_success_true_when_only_skips(self) -> None:
        """BatchSetupResults.success is True even when actions are skipped."""
        r1 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
        )
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1])],
        )

        assert batch.success is True
        assert batch.total_skipped == 1

    def test_total_succeeded_excludes_skipped(self) -> None:
        """total_succeeded does not count skipped results."""
        r1 = self._make_result(PluginKind.PACKAGE, skipped=False)  # succeeded
        r2 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
        )

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2])],
        )

        assert batch.total_succeeded == 1
        assert batch.total_skipped == 1
        assert batch.total_failed == 0
