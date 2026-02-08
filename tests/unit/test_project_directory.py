"""Tests for project_directory and SkipReason functionality."""

import json
import tempfile
from pathlib import Path

from porringer.api import API
from porringer.schema import (
    BatchSetupResults,
    SetupAction,
    SetupActionResult,
    SetupActionType,
    SetupParameters,
    SetupResults,
    SkipReason,
)
from tests.conftest import execute_via_stream


class TestProjectDirectorySkip:
    """Tests for project_directory=False skipping PROJECT_SYNC and RUN_COMMAND actions."""

    @staticmethod
    def test_false_skips_post_sync(test_api: API) -> None:
        """Post-sync commands are skipped when project_directory is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), project_directory=False)
            preview = test_api.sync.preview_batch(params)

            # Preview should still show all actions (2: 1 package + 1 command)
            assert preview.total_actions == 2

            results = execute_via_stream(test_api, preview, params)

            # The RUN_COMMAND should be skipped
            command_results = [r for r in results.skips if r.action.action_type == SetupActionType.RUN_COMMAND]
            assert len(command_results) == 1
            assert command_results[0].skipped is True
            assert command_results[0].skip_reason == SkipReason.NO_PROJECT_DIRECTORY
            assert command_results[0].message == 'No project directory for post-sync command'

    @staticmethod
    def test_false_keeps_package_actions(test_api: API) -> None:
        """Package actions still execute when project_directory is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), project_directory=False)
            preview = test_api.sync.preview_batch(params)
            results = execute_via_stream(test_api, preview, params)

            # Package action should not be skipped due to project_directory
            package_results = [
                r
                for mr in results.manifest_results
                for r in mr.results
                if r.action.action_type == SetupActionType.PACKAGE
            ]
            assert len(package_results) == 1
            project_skips = [
                r for r in package_results if r.skipped and r.skip_reason == SkipReason.NO_PROJECT_DIRECTORY
            ]
            assert len(project_skips) == 0

    @staticmethod
    def test_none_runs_post_sync(test_api: API) -> None:
        """Post-sync commands execute normally when project_directory is None (default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {
                'version': '1',
                'state': {'python': ['requests']},
                'post_sync': ['echo hello'],
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir))
            assert params.project_directory is None

            preview = test_api.sync.preview_batch(params)
            results = execute_via_stream(test_api, preview, params)

            # The RUN_COMMAND should NOT be in skips
            command_skips = [r for r in results.skips if r.action.action_type == SetupActionType.RUN_COMMAND]
            assert len(command_skips) == 0


class TestProjectDirectory:
    """Tests for project_directory tri-state defaults."""

    @staticmethod
    def test_default_is_none() -> None:
        """project_directory defaults to None (infer from manifest path)."""
        params = SetupParameters()
        assert params.project_directory is None

    @staticmethod
    def test_can_be_set_to_path() -> None:
        """project_directory can be set to a Path."""
        params = SetupParameters(project_directory=Path('/some/dir'))
        assert params.project_directory == Path('/some/dir')

    @staticmethod
    def test_can_be_set_to_false() -> None:
        """project_directory can be set to False to skip project actions."""
        params = SetupParameters(project_directory=False)
        assert params.project_directory is False


class TestBatchSetupResultsSkips:
    """Tests for BatchSetupResults.skips, total_skipped, and total_succeeded."""

    @staticmethod
    def _make_result(
        action_type: SetupActionType,
        skipped: bool = False,
        skip_reason: SkipReason | None = None,
        message: str | None = None,
    ) -> SetupActionResult:
        action = SetupAction(
            action_type=action_type,
            description=f'Test {action_type.name}',
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
        r1 = self._make_result(SetupActionType.PACKAGE, skipped=False)
        r2 = self._make_result(
            SetupActionType.PROJECT_SYNC,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory provided',
        )
        r3 = self._make_result(
            SetupActionType.RUN_COMMAND,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory for post-sync command',
        )

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2, r3])],
        )

        assert batch.total_skipped == 2
        assert len(batch.skips) == 2
        assert all(r.skipped for r in batch.skips)
        assert batch.skips[0].skip_reason == SkipReason.NO_PROJECT_DIRECTORY
        assert batch.skips[1].skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_skips_empty_when_none_skipped(self) -> None:
        """The skips property returns empty list when nothing is skipped."""
        r1 = self._make_result(SetupActionType.PACKAGE)
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1])],
        )

        assert batch.total_skipped == 0
        assert batch.skips == []

    def test_skips_carries_action_metadata(self) -> None:
        """Each skipped result carries full action metadata."""
        action = SetupAction(
            action_type=SetupActionType.PROJECT_SYNC,
            description='Sync project via uv',
            backend='python-project',
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
        assert skip.action.action_type == SetupActionType.PROJECT_SYNC
        assert skip.action.backend == 'python-project'
        assert skip.action.installer == 'uv'
        assert skip.skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_success_true_when_only_skips(self) -> None:
        """BatchSetupResults.success is True even when actions are skipped."""
        r1 = self._make_result(
            SetupActionType.PROJECT_SYNC,
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
        r1 = self._make_result(SetupActionType.PACKAGE, skipped=False)  # succeeded
        r2 = self._make_result(
            SetupActionType.PROJECT_SYNC,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
        )

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2])],
        )

        assert batch.total_succeeded == 1
        assert batch.total_skipped == 1
        assert batch.total_failed == 0
