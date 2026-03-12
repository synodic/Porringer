"""Progress streaming schemas."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from porringer.core.schema import PluginKind
from porringer.schema.execution import SetupAction, SetupActionResult, SetupResults
from porringer.schema.plugin import PluginCapability


@dataclass(slots=True)
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


@dataclass(slots=True, frozen=True)
class DiscoveredPluginEntry:
    """A single discovered plugin with availability and capabilities.

    Args:
        name: The canonical plugin name.
        available: Whether the plugin's tool binary is on PATH.
        capabilities: Protocol capabilities the plugin implements.
        kind: The plugin kind (package, tool, runtime, etc.).
    """

    name: str
    available: bool
    capabilities: frozenset[PluginCapability]
    kind: PluginKind


# ---------------------------------------------------------------------------
# Discriminated progress-event union
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ManifestLoadedEvent:
    """Emitted when a manifest has been fully resolved and is ready for execution."""

    manifest: SetupResults


@dataclass(slots=True)
class ManifestFailedEvent:
    """Emitted when a manifest path could not be loaded."""

    failed_path: tuple[Path, str]


@dataclass(slots=True)
class PluginsDiscoveredEvent:
    """Emitted once per batch with the full plugin availability map."""

    discovered_plugins: tuple[DiscoveredPluginEntry, ...]


@dataclass(slots=True)
class ManifestParsedEvent:
    """Emitted after a manifest is parsed but before execution (fast preview)."""

    manifest: SetupResults


@dataclass(slots=True)
class ActionStartedEvent:
    """Emitted when an action begins execution."""

    action: SetupAction
    action_index: int | None = None


@dataclass(slots=True)
class ActionCompletedEvent:
    """Emitted when an action finishes execution."""

    action: SetupAction
    result: SetupActionResult
    action_index: int | None = None


@dataclass(slots=True)
class SubActionProgressEvent:
    """Emitted for fine-grained progress within an action."""

    action: SetupAction
    sub_action: SubActionProgress


type ProgressEvent = (
    ManifestLoadedEvent
    | ManifestFailedEvent
    | PluginsDiscoveredEvent
    | ManifestParsedEvent
    | ActionStartedEvent
    | ActionCompletedEvent
    | SubActionProgressEvent
)


@dataclass(slots=True)
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
