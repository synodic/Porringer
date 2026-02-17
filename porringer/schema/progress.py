"""Progress streaming schemas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Literal

from porringer.schema.execution import SetupAction, SetupActionResult, SetupResults


class ProgressEventKind(Enum):
    """The kind of progress event emitted during setup execution."""

    MANIFEST_LOADED = auto()
    MANIFEST_FAILED = auto()
    ACTION_STARTED = auto()
    ACTION_COMPLETED = auto()
    SUB_ACTION_PROGRESS = auto()


@dataclass
class SubActionProgress:
    """Fine-grained progress update from within a plugin operation.

    Plugins emit these to report phases and percentages during long-running
    operations (e.g., downloading a wheel, verifying checksums).

    Args:
        action: The parent setup action this progress belongs to.
        phase: Current phase (e.g. `"downloading"`, `"installing"`, `"verifying"`).
        progress: 0.0–1.0 completion fraction, or `None` if indeterminate.
        message: Human-readable status line (e.g. `"Downloading ruff-0.8.0.whl (2.1 MB)"`).
        output: Raw output line from the subprocess, for log panel display.
        stream: Which subprocess stream the output came from (`"stdout"` or `"stderr"`).
    """

    action: SetupAction
    phase: str
    progress: float | None = None
    message: str | None = None
    output: str | None = None
    stream: Literal['stdout', 'stderr'] | None = None


@dataclass
class ProgressEvent:
    """A single progress event from the setup execution stream.

    Consumers iterate over `AsyncIterator[ProgressEvent]` to observe
    action lifecycle and sub-action detail updates.

    Args:
        kind: What this event represents.
        action: The setup action this event relates to (`None` for `MANIFEST_LOADED` / `MANIFEST_FAILED`).
        result: Action result (set only for `ACTION_COMPLETED`).
        sub_action: Sub-action detail (set only for `SUB_ACTION_PROGRESS`).
        manifest: Per-manifest preview (set only for `MANIFEST_LOADED`).
        failed_path: Path and error message (set only for `MANIFEST_FAILED`).
    """

    kind: ProgressEventKind
    action: SetupAction | None = None
    result: SetupActionResult | None = None
    sub_action: SubActionProgress | None = None
    manifest: SetupResults | None = None
    failed_path: tuple[Path, str] | None = None


@dataclass
class CancellationToken:
    """Token for cooperative cancellation of async operations.

    Used by GUI applications to request cancellation of long-running
    async operations like batch installs.

    Example:
        token = CancellationToken()
        task = asyncio.create_task(long_operation(token))
        # Later...
        token.cancel()
    """

    _cancelled: bool = field(default=False, init=False)
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def cancel(self) -> None:
        """Request cancellation of the operation."""
        self._cancelled = True
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled

    async def wait_cancelled(self) -> None:
        """Wait until cancellation is requested."""
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        """Raise asyncio.CancelledError if cancellation was requested."""
        if self._cancelled:
            raise asyncio.CancelledError('Operation cancelled by token')
