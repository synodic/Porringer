"""Tests for progress event stream and sub-action progress."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from porringer.backend.command.sync import SyncCommands
from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.schema import Ecosystem, PackageRef, PluginKind
from porringer.plugin.pip.plugin import PIPEnvironment
from porringer.schema import (
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SubActionProgress,
)

HALF_PROGRESS = 0.5
MIN_DOWNLOAD_UPDATES = 2
MIN_STREAM_EVENTS = 2


def _make_action(package: str = 'requests') -> SetupAction:
    """Create a test SetupAction."""
    return SetupAction(
        description=f'Install {package}',
        kind=PluginKind.PACKAGE,
        ecosystem=Ecosystem('python'),
        installer='pip',
        package=PackageRef(name=package),
    )


class TestSubActionProgress:
    """Tests for SubActionProgress dataclass."""

    @staticmethod
    def test_basic_construction() -> None:
        """SubActionProgress stores provided fields."""
        action = _make_action()
        progress = SubActionProgress(
            action=action,
            phase='downloading',
            progress=HALF_PROGRESS,
            message='Downloading ruff',
        )
        assert progress.action is action
        assert progress.phase == 'downloading'
        assert progress.progress == HALF_PROGRESS
        assert progress.message == 'Downloading ruff'

    @staticmethod
    def test_defaults() -> None:
        """SubActionProgress defaults optional fields."""
        action = _make_action()
        progress = SubActionProgress(action=action, phase='resolving')
        assert progress.progress is None
        assert progress.message is None

    @staticmethod
    def test_indeterminate_progress() -> None:
        """SubActionProgress accepts None progress."""
        action = _make_action()
        progress = SubActionProgress(action=action, phase='installing', progress=None, message='Installing packages')
        assert progress.progress is None


class TestProgressEvent:
    """Tests for ProgressEvent dataclass."""

    @staticmethod
    def test_action_started() -> None:
        """ACTION_STARTED event populates expected fields."""
        action = _make_action()
        event = ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action)
        assert event.kind == ProgressEventKind.ACTION_STARTED
        assert event.action is action
        assert event.result is None
        assert event.sub_action is None

    @staticmethod
    def test_action_completed() -> None:
        """ACTION_COMPLETED event includes result."""
        action = _make_action()
        result = SetupActionResult(action=action, success=True, message='ok')
        event = ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
        assert event.kind == ProgressEventKind.ACTION_COMPLETED
        assert event.result is result

    @staticmethod
    def test_sub_action_progress() -> None:
        """SUB_ACTION_PROGRESS event includes sub-action."""
        action = _make_action()
        sub = SubActionProgress(action=action, phase='downloading', progress=HALF_PROGRESS, message='pkg')
        event = ProgressEvent(kind=ProgressEventKind.SUB_ACTION_PROGRESS, action=action, sub_action=sub)
        assert event.kind == ProgressEventKind.SUB_ACTION_PROGRESS
        assert event.sub_action is sub

    @staticmethod
    def test_event_kind_values() -> None:
        """All expected enum members exist."""
        assert ProgressEventKind.ACTION_STARTED
        assert ProgressEventKind.ACTION_COMPLETED
        assert ProgressEventKind.SUB_ACTION_PROGRESS

    @staticmethod
    def test_event_defaults() -> None:
        """ProgressEvent defaults optional fields to None."""
        action = _make_action()
        event = ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action)
        assert event.result is None
        assert event.sub_action is None


class TestPackageParametersProgressCallback:
    """Tests for progress_callback on PackageParameters."""

    @staticmethod
    def test_default_is_none() -> None:
        """PackageParameters default progress_callback is None."""
        params = PackageParameters(package=PackageRef(name='requests'))
        assert params.progress_callback is None

    @staticmethod
    def test_accepts_callback() -> None:
        """PackageParameters accepts a progress callback."""
        cb = MagicMock()
        params = PackageParameters(package=PackageRef(name='requests'), progress_callback=cb)
        assert params.progress_callback is cb

    @staticmethod
    def test_callback_excluded_from_serialization() -> None:
        """Progress callback is excluded from serialization."""
        cb = MagicMock()
        params = PackageParameters(package=PackageRef(name='requests'), progress_callback=cb)
        data = params.model_dump()
        assert 'progress_callback' not in data


class TestPipProgressLineParsing:
    """Tests for PIPEnvironment._parse_progress_line."""

    @staticmethod
    def test_downloading_line() -> None:
        """Pip parser captures download start lines."""
        action = _make_action()
        collected: list[SubActionProgress] = []

        PIPEnvironment._parse_progress_line(
            'Downloading https://files.pythonhosted.org/ruff-0.8.0-py3-none-any.whl (2.1 MB)',
            action,
            collected.append,
        )

        assert len(collected) == 1
        assert collected[0].phase == 'downloading'
        assert collected[0].progress == 0.0
        assert 'ruff-0.8.0' in (collected[0].message or '')

    @staticmethod
    def test_progress_percentage_line() -> None:
        """Pip parser captures download percentages."""
        action = _make_action()
        collected: list[SubActionProgress] = []

        PIPEnvironment._parse_progress_line(
            '   1.5 MB 50%',
            action,
            collected.append,
        )

        assert len(collected) == 1
        assert collected[0].phase == 'downloading'
        assert collected[0].progress == pytest.approx(HALF_PROGRESS)

    @staticmethod
    def test_installing_line() -> None:
        """Pip parser captures installing lines."""
        action = _make_action()
        collected: list[SubActionProgress] = []

        PIPEnvironment._parse_progress_line(
            'Installing collected packages: requests, urllib3',
            action,
            collected.append,
        )

        assert len(collected) == 1
        assert collected[0].phase == 'installing'
        assert 'requests' in (collected[0].message or '')

    @staticmethod
    def test_already_satisfied_line() -> None:
        """Pip parser captures already satisfied lines."""
        action = _make_action()
        collected: list[SubActionProgress] = []

        PIPEnvironment._parse_progress_line(
            'Requirement already satisfied: requests in /usr/lib/python3.12/site-packages',
            action,
            collected.append,
        )

        assert len(collected) == 1
        assert collected[0].phase == 'verifying'
        assert collected[0].progress == 1.0

    @staticmethod
    def test_irrelevant_line_is_ignored() -> None:
        """Pip parser ignores unrelated lines."""
        action = _make_action()
        collected: list[SubActionProgress] = []

        PIPEnvironment._parse_progress_line(
            'Using cached requests-2.31.0.tar.gz',
            action,
            collected.append,
        )

        assert len(collected) == 0

    @staticmethod
    def test_full_progress_sequence() -> None:
        """Simulate a realistic sequence of pip output lines."""
        action = _make_action('ruff')
        collected: list[SubActionProgress] = []

        lines = [
            'Collecting ruff',
            'Downloading https://files.pythonhosted.org/ruff-0.8.0-py3-none-any.whl (2.1 MB)',
            '   0.5 MB 25%',
            '   1.0 MB 50%',
            '   1.5 MB 75%',
            '   2.1 MB 100%',
            'Installing collected packages: ruff',
            'Successfully installed ruff-0.8.0',
        ]

        for line in lines:
            PIPEnvironment._parse_progress_line(line, action, collected.append)

        phases = [u.phase for u in collected]
        assert 'downloading' in phases
        assert 'installing' in phases

        # Check download progress increases
        download_updates = [u for u in collected if u.phase == 'downloading']
        assert len(download_updates) >= MIN_DOWNLOAD_UPDATES
        progresses = [u.progress for u in download_updates if u.progress is not None]
        assert progresses == sorted(progresses)  # monotonically increasing


class TestExecuteStream:
    """Tests for execute_stream async generator."""

    @staticmethod
    async def test_stream_yields_events() -> None:
        """execute_stream yields ProgressEvent items via the queue-based bridge."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            commands = SyncCommands()

            collected: list[ProgressEvent] = []
            async for event in commands.execute_stream(params):
                collected.append(event)

            events = collected

            # Should have at least a MANIFEST_LOADED event + start/complete pairs
            manifest_events = [e for e in events if e.kind == ProgressEventKind.MANIFEST_LOADED]
            assert len(manifest_events) >= 1

            started = [e for e in events if e.kind == ProgressEventKind.ACTION_STARTED]
            completed = [e for e in events if e.kind == ProgressEventKind.ACTION_COMPLETED]
            assert len(started) == len(completed)

    @staticmethod
    async def test_stream_cancellation() -> None:
        """Breaking from the stream cancels the background task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests', 'flask', 'pytest', 'ruff', 'black']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), dry_run=True)
            commands = SyncCommands()

            collected: list[ProgressEvent] = []
            async for event in commands.execute_stream(params):
                collected.append(event)
                if len(collected) >= MIN_STREAM_EVENTS:
                    break  # early exit

            events = collected
            assert len(events) >= MIN_STREAM_EVENTS
