"""Utility definitions"""

import asyncio
import contextlib
import os
import re
import subprocess
import sys
from typing import Any, NamedTuple, NewType

TypeName = NewType('TypeName', str)
TypeGroup = NewType('TypeGroup', str)


class AsyncRunResult(NamedTuple):
    """Result of an async subprocess run."""

    returncode: int
    stdout: str
    stderr: str


async def async_run_command(
    args: list[str],
    *,
    timeout: float | None = None,
    cancellation_check: asyncio.Event | None = None,
) -> AsyncRunResult:
    """Run a command asynchronously using asyncio subprocess.

    Uses modern asyncio.timeout() context manager (Python 3.11+) for
    structured timeout handling.

    Args:
        args: Command and arguments to run.
        timeout: Optional timeout in seconds.
        cancellation_check: Optional Event that, when set, triggers cancellation.

    Returns:
        AsyncRunResult with returncode, stdout, and stderr.

    Raises:
        asyncio.TimeoutError: If the command times out.
        asyncio.CancelledError: If cancelled via cancellation_check.
        FileNotFoundError: If the command is not found.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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

    return AsyncRunResult(
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode('utf-8', errors='replace'),
        stderr=stderr_bytes.decode('utf-8', errors='replace'),
    )


class RunResult(NamedTuple):
    """Result of a subprocess run."""

    returncode: int
    stdout: str
    stderr: str


def run_command(
    args: list[str],
    *,
    timeout: float | None = None,
) -> RunResult:
    """Run a command synchronously with standard output capture.

    Args:
        args: Command and arguments to run.
        timeout: Optional timeout in seconds.

    Returns:
        RunResult with returncode, stdout, and stderr.

    Raises:
        subprocess.TimeoutExpired: If the command times out.
        FileNotFoundError: If the command is not found.
    """
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )

    return RunResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


class TypeID(NamedTuple):
    """Represents a type ID with a name and group."""

    name: TypeName
    group: TypeGroup


_canonicalize_regex = re.compile(r'((?<=[a-z])[A-Z]|(?<!\A)[A-Z](?=[a-z]))')


def canonicalize_name(name: str) -> TypeID:
    """Extracts the type identifier from an input string

    Args:
        name: The string to parse

    Returns:
        The type identifier
    """
    sub = re.sub(_canonicalize_regex, r' \1', name)
    values = sub.split(' ')
    result = ''.join(values[:-1])
    return TypeID(TypeName(result.lower()), TypeGroup(values[-1].lower()))


def canonicalize_type(input_type: type[Any]) -> TypeID:
    """Extracts the plugin identifier from a type

    Args:
        input_type: The input type to resolve

    Returns:
        The type identifier
    """
    return canonicalize_name(input_type.__name__)


def is_pipx_installation() -> bool:
    """Check if Porringer is installed via pipx.

    Determines whether the current Python executable is running inside
    a pipx-managed virtual environment by checking the path structure.

    Returns:
        True if running in a pipx venv, False otherwise.
    """
    return sys.prefix.split(os.sep)[-3:-1] == ['pipx', 'venvs']
