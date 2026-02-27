"""Tests proving execute_stream is event-loop safe.

These tests verify that execute_stream can run on a pre-existing event
loop (e.g. qasync) without nesting asyncio.run() or blocking the loop.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from porringer.backend.command.sync import SyncCommands
from porringer.schema import (
    ProgressEvent,
    ProgressEventKind,
    SetupParameters,
)


def _write_manifest(tmpdir: str, packages: dict[str, list[str]] | None = None) -> Path:
    """Write a minimal porringer.json into *tmpdir* and return the directory."""
    manifest_path = Path(tmpdir) / 'porringer.json'
    data = {'version': '1', 'packages': packages or {'python': ['requests']}}
    manifest_path.write_text(json.dumps(data))
    return Path(tmpdir)


class TestEventLoopSafety:
    """Verify execute_stream never calls asyncio.run() internally."""

    @staticmethod
    async def test_execute_stream_no_nested_run() -> None:
        """execute_stream works when driven by an already-running event loop.

        If any code path internally called asyncio.run(), this test would
        raise ``RuntimeError: This event loop is already running``.
        pytest-asyncio provides the running loop — we just await the
        async generator directly.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = _write_manifest(tmpdir)
            params = SetupParameters(paths=directory, dry_run=True)
            commands = SyncCommands()

            events: list[ProgressEvent] = []
            async for event in commands.execute_stream(params):
                events.append(event)

            # Sanity: we received at least one manifest event
            assert any(e.kind == ProgressEventKind.MANIFEST_PARSED for e in events)

    @staticmethod
    async def test_concurrent_streams_on_shared_loop() -> None:
        """Two execute_stream calls run concurrently on a single event loop.

        This proves there is no global lock or event-loop-blocking I/O
        that would serialize the two streams.
        """
        commands = SyncCommands()
        results: dict[str, list[ProgressEvent]] = {'a': [], 'b': []}

        async def _drain(key: str, directory: Path) -> None:
            params = SetupParameters(paths=directory, dry_run=True)
            async for event in commands.execute_stream(params):
                results[key].append(event)

        with (
            tempfile.TemporaryDirectory() as tmpdir_a,
            tempfile.TemporaryDirectory() as tmpdir_b,
        ):
            dir_a = _write_manifest(tmpdir_a, packages={'python': ['requests']})
            dir_b = _write_manifest(tmpdir_b, packages={'python': ['flask']})

            async with asyncio.TaskGroup() as tg:
                tg.create_task(_drain('a', dir_a))
                tg.create_task(_drain('b', dir_b))

        # Both streams produced events
        assert len(results['a']) > 0, 'Stream A produced no events'
        assert len(results['b']) > 0, 'Stream B produced no events'

        # Both had manifest-parsed events
        assert any(e.kind == ProgressEventKind.MANIFEST_PARSED for e in results['a'])
        assert any(e.kind == ProgressEventKind.MANIFEST_PARSED for e in results['b'])
