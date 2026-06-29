"""Tests for the pytest-subprocess command-process wrapper."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest
from dirty_equals import IsPartialDict
from pytest_subprocess import ProcessNotRegisteredError

from tests.fixtures.command_process import CommandProcess


async def test_records_argv_cwd_and_env(command_process: CommandProcess, tmp_path) -> None:
    """A registered call captures argv, cwd and selected env values."""
    env = {'PATH': str(tmp_path / 'bin'), 'IGNORED': 'value'}
    command_process.script(['git', 'status'])

    await asyncio.create_subprocess_exec('git', 'status', cwd=tmp_path, env=env)

    command_process.assert_called_with(['git', 'status'], cwd=str(tmp_path), env=env)
    assert [asdict(call) for call in command_process.calls] == [
        IsPartialDict(
            argv=('git', 'status'),
            cwd=str(tmp_path),
            env=IsPartialDict(PATH=str(tmp_path / 'bin')),
            returncode=0,
        )
    ]


async def test_script_prefix_returns_canned_output(command_process: CommandProcess) -> None:
    """A scripted prefix returns its canned stdout bytes."""
    command_process.script_prefix(['npm', 'ls'], stdout='{"deps": {}}')

    proc = await asyncio.create_subprocess_exec(
        'npm',
        'ls',
        '-g',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()

    assert out == b'{"deps": {}}'
    assert err == b''
    assert proc.returncode == 0
    assert command_process.argv_list == [['npm', 'ls', '-g']]


async def test_first_matching_script_wins(command_process: CommandProcess) -> None:
    """Scripts use pytest-subprocess matching order, so specific prefixes can win."""
    command_process.script_prefix(['git', 'remote'], stdout='origin')
    command_process.script_prefix(['git'], stdout='generic')

    proc = await asyncio.create_subprocess_exec('git', 'remote', '-v', stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()

    assert out == b'origin'


async def test_unregistered_call_raises(command_process: CommandProcess) -> None:
    """Unregistered subprocess calls fail by default."""
    with pytest.raises(ProcessNotRegisteredError, match='was not registered'):
        await asyncio.create_subprocess_exec('uv', 'pip', 'install', 'x')
    command_process.assert_no_calls()


async def test_assert_called_with_reports_observed_calls_on_miss(command_process: CommandProcess) -> None:
    """``assert_called_with`` raises and lists observed calls when there is no match."""
    command_process.script(['git', 'status'])
    await asyncio.create_subprocess_exec('git', 'status')

    with pytest.raises(AssertionError, match='No call matching'):
        command_process.assert_called_with(['git', 'log'])


def test_assert_no_calls_passes_when_unused(command_process: CommandProcess) -> None:
    """``assert_no_calls`` succeeds when no registered calls were observed."""
    command_process.assert_no_calls()
