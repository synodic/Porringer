"""Helpers for test trace."""

"""Tests for opt-in command trace artifacts."""

import json
import sys

import pytest
from dirty_equals import IsInstance, IsPartialDict, IsStr

from porringer.schema import SCHEMA_VERSION, ActionRef
from porringer.utility.trace import TraceContext, format_trace_summary, use_trace_context
from porringer.utility.utility import run_command


async def test_run_command_writes_trace_when_enabled(tmp_path, monkeypatch) -> None:
    """``PORRINGER_TRACE_DIR`` writes one JSON trace per command."""
    monkeypatch.setenv('PORRINGER_TRACE_DIR', str(tmp_path))

    result = await run_command([sys.executable, '-c', 'print("trace-ok")'])

    assert result.returncode == 0
    traces = list(tmp_path.glob('*.json'))
    assert len(traces) == 1
    trace_text = traces[0].read_text(encoding='utf-8')
    payload = json.loads(trace_text)
    assert payload['argv'][:3] == [sys.executable, '-c', 'print("trace-ok")']
    assert payload['returncode'] == 0
    assert payload['trace_schema_version'] == SCHEMA_VERSION
    assert payload['action_ref'] is None
    assert payload['stdout_tail'].strip() == 'trace-ok'
    assert payload['env_subset']['PORRINGER_TRACE_DIR'] == str(tmp_path)
    assert payload == IsPartialDict(
        argv=[sys.executable, '-c', 'print("trace-ok")'],
        cwd=None,
        duration_ms=IsInstance(float),
        env_subset=IsPartialDict(PORRINGER_TRACE_DIR=str(tmp_path)),
        error=None,
        returncode=0,
        stderr_tail='',
        stdout_tail=IsStr(regex=r'trace-ok\s*'),
        which_after=IsStr(min_length=1),
        which_before=IsStr(min_length=1),
    )
    assert '\n' not in trace_text


async def test_trace_directory_cache_tracks_env_changes(tmp_path, monkeypatch) -> None:
    """Changing ``PORRINGER_TRACE_DIR`` writes to the new directory."""
    first = tmp_path / 'first'
    second = tmp_path / 'second'

    monkeypatch.setenv('PORRINGER_TRACE_DIR', str(first))
    assert (await run_command([sys.executable, '-c', 'print("first")'])).returncode == 0

    monkeypatch.setenv('PORRINGER_TRACE_DIR', str(second))
    assert (await run_command([sys.executable, '-c', 'print("second")'])).returncode == 0

    assert len(tuple(first.glob('*.json'))) == 1
    assert len(tuple(second.glob('*.json'))) == 1


async def test_run_command_writes_trace_when_launch_fails(tmp_path, monkeypatch) -> None:
    """Launch failures still leave a trace artifact for diagnostics."""
    monkeypatch.setenv('PORRINGER_TRACE_DIR', str(tmp_path))
    missing_executable = tmp_path / 'missing-command'

    with pytest.raises((FileNotFoundError, OSError)):
        await run_command([str(missing_executable)])

    traces = list(tmp_path.glob('*.json'))
    assert len(traces) == 1
    payload = json.loads(traces[0].read_text(encoding='utf-8'))
    assert payload['argv'] == [str(missing_executable)]
    assert payload['returncode'] is None
    assert payload['trace_schema_version'] == SCHEMA_VERSION
    assert payload['error']
    assert payload == IsPartialDict(
        argv=[str(missing_executable)],
        cwd=None,
        duration_ms=IsInstance(float),
        env_subset=IsPartialDict(PORRINGER_TRACE_DIR=str(tmp_path)),
        error=IsStr(min_length=1),
        returncode=None,
        stderr_tail='',
        stdout_tail='',
        which_after=None,
        which_before=None,
    )


async def test_run_command_writes_action_trace_context(tmp_path, monkeypatch) -> None:
    """Active trace context is copied into command trace artifacts."""
    monkeypatch.setenv('PORRINGER_TRACE_DIR', str(tmp_path))
    ref = ActionRef.from_indices(1, 2)

    with use_trace_context(
        TraceContext(
            mode='inspect',
            action_ref=ref,
            action_description='Install demo',
            action_kind='packages',
            installer='pip',
            package_name='demo',
            operation='presence',
        )
    ):
        result = await run_command([sys.executable, '-c', 'print("context-ok")'])

    assert result.returncode == 0
    traces = list(tmp_path.glob('*.json'))
    assert len(traces) == 1
    payload = json.loads(traces[0].read_text(encoding='utf-8'))
    assert payload['mode'] == 'inspect'
    assert payload['action_id'] == '1:2'
    assert payload['action_ref'] == {'manifest_index': 1, 'action_index': 2, 'action_id': '1:2'}
    assert payload['action'] == {
        'description': 'Install demo',
        'kind': 'packages',
        'installer': 'pip',
        'package_name': 'demo',
    }
    assert payload['operation'] == 'presence'


def test_format_trace_summary_reports_recent_commands(tmp_path) -> None:
    """Trace summaries are formatted next to the trace payload contract."""
    trace = tmp_path / 'trace.json'
    trace.write_text(
        json.dumps({
            'argv': ['tool', 'install', 'demo'],
            'returncode': 1,
            'stderr_tail': 'failed loudly\nlast line',
            'error': None,
        }),
        encoding='utf-8',
    )

    summary = format_trace_summary(tmp_path)

    assert f'trace directory: {tmp_path}' in summary
    assert 'command traces: 1' in summary
    assert 'rc=1 tool install demo' in summary
    assert 'stderr: last line' in summary
    assert summary.splitlines() == [
        f'trace directory: {tmp_path}',
        'command traces: 1',
        '- trace.json: rc=1 tool install demo',
        '  stderr: last line',
    ]


def test_format_trace_summary_reports_unreadable_trace_file(tmp_path) -> None:
    """Unreadable trace payloads are summarized without raising."""
    trace = tmp_path / 'bad.json'
    trace.write_text('{', encoding='utf-8')

    summary = format_trace_summary(tmp_path)

    assert f'trace directory: {tmp_path}' in summary
    assert '- bad.json: unreadable' in summary
