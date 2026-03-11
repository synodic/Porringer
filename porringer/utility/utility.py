"""Utility definitions"""

import asyncio
import contextlib
import os
import sys
from collections.abc import Callable, Sequence
from typing import Literal, NamedTuple

from porringer.schema import SetupAction, SubActionProgress

__all__ = [
    'CommandResult',
    'StreamProgress',
    'is_pipx_installation',
    'run_command',
    'stream_command',
]


class CommandResult(NamedTuple):
    """Result of a subprocess run (sync or async)."""

    returncode: int
    stdout: str
    stderr: str


class StreamProgress(NamedTuple):
    """Configuration for streaming progress reporting.

    Groups the progress-related parameters used by `stream_command`.
    """

    action: SetupAction
    callback: Callable[[SubActionProgress], None]
    phase: str = 'running'


async def run_command(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
    cancellation_check: asyncio.Event | None = None,
) -> CommandResult:
    """Run a command asynchronously using asyncio subprocess.

    Uses modern asyncio.timeout() context manager (Python 3.11+) for
    structured timeout handling.

    Args:
        args: Command and arguments to run.
        cwd: Optional working directory for the subprocess.
        timeout: Optional timeout in seconds.
        cancellation_check: Optional Event that, when set, triggers cancellation.

    Returns:
        CommandResult with returncode, stdout, and stderr.

    Raises:
        asyncio.TimeoutError: If the command times out.
        asyncio.CancelledError: If cancelled via cancellation_check.
        FileNotFoundError: If the command is not found.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    async def communicate_or_cancel() -> tuple[bytes, bytes]:
        """Communicate with process, checking for cancellation."""
        if cancellation_check is None:
            return await process.communicate()

        # Race between process completion and cancellation
        communicate_task = asyncio.create_task(process.communicate())
        cancel_task = asyncio.create_task(cancellation_check.wait())

        done, pending = await asyncio.wait(
            [communicate_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if cancel_task in done:
            # Cancellation requested - kill process
            process.kill()
            await process.wait()
            raise asyncio.CancelledError('Operation cancelled')

        return communicate_task.result()

    try:
        async with asyncio.timeout(timeout):
            stdout_bytes, stderr_bytes = await communicate_or_cancel()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise

    return CommandResult(
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode('utf-8', errors='replace'),
        stderr=stderr_bytes.decode('utf-8', errors='replace'),
    )


async def stream_command(
    args: Sequence[str],
    *,
    progress: StreamProgress,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
    cancellation_check: asyncio.Event | None = None,
) -> CommandResult:
    """Run a command asynchronously, streaming stdout/stderr line-by-line.

    Each line of output is emitted as a `SubActionProgress` event
    with the `output` and `stream` fields set, enabling real-time
    display in an execution log panel.

    Args:
        args: Command and arguments to run.
        progress: Streaming progress configuration (action, callback, phase).
        cwd: Optional working directory for the subprocess.
        timeout: Optional timeout in seconds.
        cancellation_check: Optional Event that, when set, triggers cancellation.

    Returns:
        CommandResult with returncode, stdout, and stderr.

    Raises:
        asyncio.TimeoutError: If the command times out.
        asyncio.CancelledError: If cancelled via cancellation_check.
        FileNotFoundError: If the command is not found.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    async def read_stream(
        stream: asyncio.StreamReader,
        stream_name: Literal['stdout', 'stderr'],
        lines: list[str],
    ) -> None:
        """Read a stream line-by-line and emit progress events."""
        async for raw_line in stream:
            line = raw_line.decode('utf-8', errors='replace').rstrip()
            lines.append(line)
            progress.callback(
                SubActionProgress(
                    action=progress.action,
                    phase=progress.phase,
                    output=line,
                    stream=stream_name,
                )
            )

    async def run_with_streaming() -> None:
        """Stream both pipes and await process completion."""
        cancel_task: asyncio.Task[None] | None = None

        if cancellation_check is not None:
            check = cancellation_check

            async def _kill_on_cancel() -> None:
                await check.wait()
                process.kill()

            cancel_task = asyncio.create_task(_kill_on_cancel())

        try:
            async with asyncio.TaskGroup() as tg:
                if process.stdout:
                    tg.create_task(read_stream(process.stdout, 'stdout', stdout_lines))
                if process.stderr:
                    tg.create_task(read_stream(process.stderr, 'stderr', stderr_lines))
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task

        await process.wait()

        if cancellation_check is not None and cancellation_check.is_set():
            raise asyncio.CancelledError('Operation cancelled')

    try:
        async with asyncio.timeout(timeout):
            await run_with_streaming()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise

    return CommandResult(
        returncode=process.returncode or 0,
        stdout='\n'.join(stdout_lines),
        stderr='\n'.join(stderr_lines),
    )


def is_pipx_installation() -> bool:
    """Check if Porringer is installed via pipx.

    Determines whether the current Python executable is running inside
    a pipx-managed virtual environment by checking the path structure.

    Returns:
        True if running in a pipx venv, False otherwise.
    """
    return sys.prefix.split(os.sep)[-3:-1] == ['pipx', 'venvs']
