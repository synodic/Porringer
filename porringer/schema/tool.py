"""Data models and schemas for tool."""

"""Managed tool/package operation schemas."""

from pydantic import Field

from porringer.core.schema import PorringerModel
from porringer.schema.inspection import FailedPathInspection
from porringer.schema.observability import SCHEMA_VERSION, Diagnostic, FollowUpAction, ResultStatus


class ManagedPackageResult(PorringerModel):
    """Stable result for a managed package check, upgrade, or uninstall."""

    plugin: str | None = None
    package: str | None = None
    kind: str | None = None
    action_id: str | None = None
    success: bool
    skipped: bool = False
    skip_reason: str | None = None
    message: str | None = None
    installed_version: str | None = None
    available_version: str | None = None


class ManagedToolReport(PorringerModel):
    """Stable report for managed tool/package operations."""

    schema_version: str = SCHEMA_VERSION
    operation: str
    status: ResultStatus = ResultStatus.SUCCESS
    manifests_processed: int = 0
    results: tuple[ManagedPackageResult, ...] = Field(default_factory=tuple)
    failed_paths: tuple[FailedPathInspection, ...] = Field(default_factory=tuple)
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)

    @property
    def success(self) -> bool:
        """Whether the operation completed without failed paths or failed package results."""
        return not self.failed_paths and all(result.success for result in self.results)

    @property
    def updated(self) -> int:
        """Number of non-skipped successful package operations."""
        return sum(1 for result in self.results if result.success and not result.skipped)

    @property
    def failed(self) -> int:
        """Number of failed package operations."""
        return sum(1 for result in self.results if not result.success)

    @property
    def skipped(self) -> int:
        """Number of skipped package operations."""
        return sum(1 for result in self.results if result.skipped)
