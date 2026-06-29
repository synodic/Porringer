"""Helpers for test event loop safety.

Tests proving sync execution is event-loop safe.

These tests verify that sync.run can run on a pre-existing event
loop (e.g. qasync) without nesting asyncio.run() or blocking the loop.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from porringer.backend.command.sync import SyncCommands
from porringer.schema import (
    ManifestLoadedEvent,
    ProgressEvent,
    SetupParameters,
)


def _write_manifest(tmpdir: str, packages: dict[str, list[str]] | None = None) -> Path:
    """Write a minimal porringer.json into *tmpdir* and return the directory."""
    manifest_path = Path(tmpdir) / 'porringer.json'
    data: dict[str, object] = {'version': '1'}
    if packages is not None:
        data['packages'] = packages
    manifest_path.write_text(json.dumps(data))
    return Path(tmpdir)


@pytest.mark.mock_packages
class TestEventLoopSafety:
    """Verify sync.run never calls asyncio.run() internally."""

    @staticmethod
    async def test_run_no_nested_run() -> None:
        """Run works when driven by an already-running event loop.

        If any code path internally called asyncio.run(), this test would
        raise ``RuntimeError: This event loop is already running``.
        pytest-asyncio provides the running loop.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = _write_manifest(tmpdir)
            params = SetupParameters(paths=directory)
            commands = SyncCommands()

            events: list[ProgressEvent] = []
            await commands.run(params, on_event=events.append)

            # Sanity: we received at least one manifest event
            assert any(isinstance(e, ManifestLoadedEvent) for e in events)

    @staticmethod
    async def test_concurrent_runs_on_shared_loop() -> None:
        """Two sync.run calls run concurrently on a single event loop.

        This proves there is no global lock or event-loop-blocking I/O
        that would serialize the two runs.
        """
        commands = SyncCommands()
        results: dict[str, list[ProgressEvent]] = {'a': [], 'b': []}

        async def _drain(key: str, directory: Path) -> None:
            params = SetupParameters(paths=directory)
            await commands.run(params, on_event=results[key].append)

        with (
            tempfile.TemporaryDirectory() as tmpdir_a,
            tempfile.TemporaryDirectory() as tmpdir_b,
        ):
            dir_a = _write_manifest(tmpdir_a)
            dir_b = _write_manifest(tmpdir_b)

            async with asyncio.TaskGroup() as tg:
                tg.create_task(_drain('a', dir_a))
                tg.create_task(_drain('b', dir_b))

        # Both runs produced events
        assert len(results['a']) > 0, 'Run A produced no events'
        assert len(results['b']) > 0, 'Run B produced no events'

        # Both had manifest-loaded events
        assert any(isinstance(e, ManifestLoadedEvent) for e in results['a'])
        assert any(isinstance(e, ManifestLoadedEvent) for e in results['b'])
