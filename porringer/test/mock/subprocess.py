"""Tests covering the subprocess behavior.

Mock subprocess helpers for tests.

Provides ``fake_proc`` — a factory for mock ``asyncio.subprocess.Process``
objects.  Replaces the five identical ``_fake_proc`` definitions that
were duplicated across test files.
"""

from unittest.mock import AsyncMock


def fake_proc(returncode: int = 0, stdout: str = '', stderr: str = '') -> AsyncMock:
    """Create a mock ``asyncio.subprocess.Process``.

    The returned mock supports ``await proc.communicate()`` and
    exposes ``proc.returncode``.

    Args:
        returncode: Exit code for the mock process.
        stdout: Decoded stdout content (will be encoded to bytes).
        stderr: Decoded stderr content (will be encoded to bytes).

    Returns:
        An ``AsyncMock`` behaving like a subprocess ``Process``.
    """
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc
