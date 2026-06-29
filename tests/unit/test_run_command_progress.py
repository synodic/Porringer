"""Helpers for test run command progress.

Tests for subprocess command progress output retention.
"""

from porringer.schema import ActionProgress, SetupAction
from porringer.utility.utility import CommandProgress, run_command
from tests.fixtures.command_process import CommandProcess

_OUTPUT_LINES = 5
_TAIL_LINES = 2


def _action() -> SetupAction:
    """Create an action descriptor for progress tests."""
    return SetupAction(description='Run chatty command')


async def test_run_command_retains_tail_and_reports_all_events(command_process: CommandProcess) -> None:
    """Returned output is bounded while progress still receives every line."""
    command_process.script(
        ['chatty'],
        stdout=[f'out-{index}' for index in range(_OUTPUT_LINES)],
        stderr=[f'err-{index}' for index in range(_OUTPUT_LINES)],
    )
    events: list[ActionProgress] = []

    result = await run_command(
        ['chatty'],
        progress=CommandProgress(action=_action(), callback=events.append),
        output_tail_lines=_TAIL_LINES,
    )

    assert [event.output for event in events if event.channel == 'stdout'] == [
        f'out-{index}' for index in range(_OUTPUT_LINES)
    ]
    assert [event.output for event in events if event.channel == 'stderr'] == [
        f'err-{index}' for index in range(_OUTPUT_LINES)
    ]
    assert result.stdout.splitlines() == [
        '[porringer omitted 3 earlier stdout line(s); retained last 2]',
        'out-3',
        'out-4',
    ]
    assert result.stderr.splitlines() == [
        '[porringer omitted 3 earlier stderr line(s); retained last 2]',
        'err-3',
        'err-4',
    ]


async def test_run_command_can_retain_full_output(command_process: CommandProcess) -> None:
    """Passing output_tail_lines=None preserves the previous full-output behavior."""
    command_process.script(['quiet'], stdout=['one', 'two', 'three'])

    result = await run_command(
        ['quiet'],
        progress=CommandProgress(action=_action(), callback=lambda _: None),
        output_tail_lines=None,
    )

    assert result.stdout == 'one\ntwo\nthree'
    assert not result.stderr
