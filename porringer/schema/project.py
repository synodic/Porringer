"""Data models and schemas for project."""

"""Project and cached-directory inspection schemas."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field

from porringer.core.schema import PorringerModel
from porringer.schema.inspection import InspectionMode, InspectionSummary, ManifestInspection
from porringer.schema.observability import SCHEMA_VERSION, Diagnostic, FollowUpAction, ResultStatus


class ProjectState(StrEnum):
    """High-level cached-project state."""

    MISSING = 'missing'
    NO_MANIFEST = 'no_manifest'
    INSPECTED = 'inspected'
    FAILED = 'failed'


class ProjectDirectorySnapshot(PorringerModel):
    """JSON-stable projection of a cached or ad-hoc project directory."""

    path: Path
    name: str | None = None
    exists: bool | None = None
    has_manifest: bool | None = None


class ProjectInspection(PorringerModel):
    """Inspection result for one project directory or manifest path."""

    schema_version: str = SCHEMA_VERSION
    directory: ProjectDirectorySnapshot
    state: ProjectState
    inspection_mode: InspectionMode = InspectionMode.FAST
    manifest: ManifestInspection | None = None
    summary: InspectionSummary = Field(default_factory=InspectionSummary)
    status: ResultStatus = ResultStatus.SUCCESS
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)
    error: str | None = None

    @property
    def success(self) -> bool:
        """Whether the project could be inspected without unavailable or failed actions."""
        return self.state == ProjectState.INSPECTED and self.summary.failed == 0 and self.summary.unavailable == 0


class ProjectInspectionSummary(PorringerModel):
    """Aggregate project inspection counts for a cached-project report."""

    projects: int = 0
    missing: int = 0
    no_manifest: int = 0
    inspected: int = 0
    failed: int = 0
    actions: int = 0
    needed: int = 0
    satisfied: int = 0
    update_available: int = 0
    unavailable: int = 0
    skipped: int = 0


class ProjectInspectionReport(PorringerModel):
    """Inspection report for cached projects."""

    schema_version: str = SCHEMA_VERSION
    operation: str = 'project.inspect_cached'
    status: ResultStatus = ResultStatus.SUCCESS
    inspection_mode: InspectionMode = InspectionMode.FAST
    projects: tuple[ProjectInspection, ...] = Field(default_factory=tuple)
    summary: ProjectInspectionSummary = Field(default_factory=ProjectInspectionSummary)
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)

    @property
    def success(self) -> bool:
        """Whether every project in the report was inspectable."""
        return self.summary.missing == 0 and self.summary.failed == 0
