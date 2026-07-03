"""Data models for stateless per-directory inspection.

These types describe the status of a manifest directory the caller
already knows about. They carry no persisted state: a downstream GUI
owns its own list of project directories and passes them to
``api.sync.inspect_paths``.
"""

from enum import StrEnum
from pathlib import Path

from porringer.core.schema import PorringerModel
from porringer.schema.inspection import InspectionSummary


class DirectoryState(StrEnum):
    """High-level status of an inspected directory."""

    MISSING = 'missing'
    NO_MANIFEST = 'no_manifest'
    INSPECTED = 'inspected'
    FAILED = 'failed'


class DirectoryStatus(PorringerModel):
    """Stateless inspection status for a single manifest directory."""

    path: Path
    name: str | None = None
    exists: bool = False
    has_manifest: bool = False
    state: DirectoryState
    summary: InspectionSummary | None = None
    error: str | None = None
