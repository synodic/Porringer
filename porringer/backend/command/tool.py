"""CLI command implementation for tool.

Managed tool/package operations over the current project's manifest.
"""

from collections.abc import Callable

from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.sync import SyncCommands
from porringer.schema import (
    BatchSetupResults,
    FailedPathInspection,
    InspectionMode,
    InspectionStatus,
    ManagedPackageResult,
    ManagedToolReport,
    ProgressEvent,
    SetupActionResult,
    SetupParameters,
    SyncStrategy,
)
from porringer.schema.inspection import ActionInspection, SyncInspectionReport
from porringer.utility.observability import (
    batch_diagnostics,
    batch_follow_up_actions,
    result_status,
)


class ToolCommands:
    """High-level managed package/tool operations for downstream clients."""

    def __init__(self, sync_commands: SyncCommands) -> None:
        """Initialize tool commands."""
        self._sync = sync_commands

    async def check_updates(
        self,
        *,
        plugins: DiscoveredPlugins | None = None,
        plugin_names: set[str] | None = None,
        include_packages: set[str] | None = None,
    ) -> ManagedToolReport:
        """Check the current project's manifest for managed packages with updates available."""
        params = SetupParameters(
            paths=None,
            inspection_mode=InspectionMode.COMPLETE,
            plugins=plugin_names,
            include_packages=include_packages,
            fail_fast=False,
        )
        report = await self._sync.inspect(params, plugins=plugins)
        return _report_from_inspection('check_updates', report)

    async def upgrade_project(
        self,
        *,
        plugins: DiscoveredPlugins | None = None,
        plugin_names: set[str] | None = None,
        include_packages: set[str] | None = None,
        on_event: Callable[[ProgressEvent], object] | None = None,
    ) -> ManagedToolReport:
        """Upgrade managed packages declared by the current project's manifest.

        Tool upgrades change package/tool state and do not
        execute separate project-command hooks.
        """
        params = SetupParameters(
            paths=None,
            strategy=SyncStrategy.LATEST,
            plugins=plugin_names,
            include_packages=include_packages,
            fail_fast=False,
        )
        report = await self._sync.run(params, plugins=plugins, on_event=on_event)
        return _report_from_batch('upgrade_project', report.results)


def _report_from_inspection(operation: str, report: SyncInspectionReport) -> ManagedToolReport:
    """Build a tool report from an inspection report."""
    results = [
        _inspection_snapshot(action)
        for manifest in report.manifests
        for action in manifest.actions
        if action.status == InspectionStatus.UPDATE_AVAILABLE
    ]
    return ManagedToolReport(
        operation=operation,
        status=result_status(report.success, report.diagnostics),
        manifests_processed=report.summary.manifests,
        results=tuple(results),
        failed_paths=report.failed_paths,
        diagnostics=report.diagnostics,
        follow_up_actions=tuple(
            action
            for action in report.follow_up_actions
            if action.target is not None and action.target.kind in {'action', 'package'}
        ),
    )


def _report_from_batch(operation: str, batch: BatchSetupResults) -> ManagedToolReport:
    """Build a tool report from collected execution results."""
    results = tuple(_result_snapshot(result) for manifest in batch.manifest_results for result in manifest.results)
    failed_paths = tuple(FailedPathInspection(path=path, error=error) for path, error in batch.failed_paths)
    diagnostics = batch_diagnostics(batch)
    return ManagedToolReport(
        operation=operation,
        status=result_status(batch.success, diagnostics),
        manifests_processed=len(batch.manifest_results),
        results=results,
        failed_paths=failed_paths,
        diagnostics=diagnostics,
        follow_up_actions=batch_follow_up_actions(batch),
    )


def _inspection_snapshot(action: ActionInspection) -> ManagedPackageResult:
    """Project an inspected package action into a tool result."""
    return ManagedPackageResult(
        plugin=action.action.installer,
        package=action.action.package_name,
        kind=action.action.kind,
        action_id=action.action_id,
        success=action.success,
        skipped=action.skipped,
        skip_reason=action.skip_reason,
        message=action.message,
        installed_version=action.installed_version,
        available_version=action.available_version,
    )


def _result_snapshot(result: SetupActionResult) -> ManagedPackageResult:
    """Project a setup action result into a tool result."""
    action = result.action
    return ManagedPackageResult(
        plugin=action.installer,
        package=action.package.name if action.package is not None else None,
        kind=action.kind.value if action.kind is not None else None,
        success=result.success,
        skipped=result.skipped,
        skip_reason=result.skip_reason.name if result.skip_reason is not None else None,
        message=result.message,
        installed_version=result.installed_version,
        available_version=result.available_version,
    )
