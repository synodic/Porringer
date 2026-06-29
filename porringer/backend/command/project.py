"""CLI command implementation for project.

Project and cached-directory inspection commands.
"""

import asyncio
from pathlib import Path

from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.backend.command.core.inspection import inspection_summary
from porringer.backend.command.sync import SyncCommands
from porringer.schema import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticTarget,
    DirectoryValidationResult,
    FollowUpAction,
    InspectionMode,
    InspectionSummary,
    ManifestDirectory,
    ProjectDirectorySnapshot,
    ProjectInspection,
    ProjectInspectionReport,
    ProjectInspectionSummary,
    ProjectState,
    ResultStatus,
    SetupParameters,
)
from porringer.schema.inspection import FailedPathInspection, ManifestInspection, SyncInspectionReport
from porringer.utility.observability import result_status


class ProjectCommands:
    """Cached-project management and inspection namespace."""

    def __init__(self, cache_manager: DirectoryCacheManager, sync_commands: SyncCommands) -> None:
        """Initialize project commands."""
        self._cache_manager = cache_manager
        self._sync = sync_commands

    async def list(
        self,
        *,
        validate: bool = True,
        check_manifest: bool = True,
    ) -> tuple[ProjectDirectorySnapshot, ...]:
        """List cached project directories."""
        results = await asyncio.to_thread(
            self._cache_manager.list_directories,
            validate=validate,
            check_manifest=check_manifest,
        )
        return tuple(_directory_snapshot(result) for result in results)

    async def inspect(
        self,
        path: str | Path,
        *,
        inspection_mode: InspectionMode = InspectionMode.FAST,
        project_directory: Path | None = None,
        plugins: DiscoveredPlugins | None = None,
    ) -> ProjectInspection:
        """Inspect one project directory or manifest path."""
        directory_path = await asyncio.to_thread(Path(path).resolve)
        exists = await asyncio.to_thread(directory_path.exists)
        has_manifest = await asyncio.to_thread(self._sync.has_manifest, directory_path) if exists else False
        validation = DirectoryValidationResult(
            directory=ManifestDirectory(path=directory_path, name=directory_path.name),
            exists=exists,
            has_manifest=has_manifest,
        )
        report = await self._inspection_from_validations(
            (validation,),
            inspection_mode=inspection_mode,
            project_directory=project_directory,
            plugins=plugins,
        )
        return report.projects[0]

    async def inspect_cached(
        self,
        *,
        inspection_mode: InspectionMode = InspectionMode.FAST,
        plugins: DiscoveredPlugins | None = None,
    ) -> ProjectInspectionReport:
        """Inspect every cached project directory with a single shared plugin context."""
        validations = await asyncio.to_thread(
            self._cache_manager.list_directories,
            validate=True,
            check_manifest=True,
        )
        return await self._inspection_from_validations(
            tuple(validations),
            inspection_mode=inspection_mode,
            plugins=plugins,
        )

    async def add(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        inspection_mode: InspectionMode = InspectionMode.FAST,
        plugins: DiscoveredPlugins | None = None,
    ) -> ProjectInspection:
        """Add a directory to the cache and return its current inspection."""
        directory = await asyncio.to_thread(self._cache_manager.add_directory, Path(path), name)
        return await self.inspect(directory.path, inspection_mode=inspection_mode, plugins=plugins)

    async def remove(self, path: str | Path) -> bool:
        """Remove a directory from the cache."""
        return await asyncio.to_thread(self._cache_manager.remove_directory, Path(path))

    async def clear(self) -> None:
        """Clear all cached project directories."""
        await asyncio.to_thread(self._cache_manager.clear)

    async def _inspection_from_validations(
        self,
        validations: tuple[DirectoryValidationResult, ...],
        *,
        inspection_mode: InspectionMode,
        project_directory: Path | None = None,
        plugins: DiscoveredPlugins | None = None,
    ) -> ProjectInspectionReport:
        inspectable = [result.directory.path for result in validations if result.exists and result.has_manifest]
        report: SyncInspectionReport | None = None
        if inspectable:
            params = SetupParameters(
                paths=inspectable,
                inspection_mode=inspection_mode,
                fail_fast=False,
                project_directory=project_directory,
            )
            report = await self._sync.inspect(params, plugins=plugins)

        projects = tuple(_project_inspections(validations, report, inspection_mode))
        diagnostics = tuple(diagnostic for project in projects for diagnostic in project.diagnostics)
        follow_up_actions = tuple(action for project in projects for action in project.follow_up_actions)
        return ProjectInspectionReport(
            inspection_mode=inspection_mode,
            projects=projects,
            summary=_project_summary(projects),
            status=result_status(all(project.success for project in projects), diagnostics),
            diagnostics=diagnostics,
            follow_up_actions=follow_up_actions,
        )


def _directory_snapshot(result: DirectoryValidationResult) -> ProjectDirectorySnapshot:
    """Project a directory validation result into a stable schema."""
    return ProjectDirectorySnapshot(
        path=result.directory.path,
        name=result.directory.name,
        exists=result.exists,
        has_manifest=result.has_manifest,
    )


def _project_inspections(
    validations: tuple[DirectoryValidationResult, ...],
    report: SyncInspectionReport | None,
    inspection_mode: InspectionMode,
) -> list[ProjectInspection]:
    """Build per-project inspections from validation and manifest inspection results."""
    failed_by_path: dict[Path, FailedPathInspection] = {}
    manifests: list[ManifestInspection] = []
    if report is not None:
        failed_by_path = {failed.path: failed for failed in report.failed_paths}
        manifests = list(report.manifests)

    inspections: list[ProjectInspection] = []
    manifest_index = 0
    for validation in validations:
        directory = _directory_snapshot(validation)
        if validation.exists is False:
            diagnostic = Diagnostic(
                code='project.missing',
                severity=DiagnosticSeverity.ERROR,
                message='Path does not exist',
                target=DiagnosticTarget(kind='project', path=validation.directory.path),
            )
            inspections.append(
                ProjectInspection(
                    directory=directory,
                    state=ProjectState.MISSING,
                    inspection_mode=inspection_mode,
                    status=ResultStatus.FAILED,
                    diagnostics=(diagnostic,),
                    error='Path does not exist',
                )
            )
            continue
        if not validation.has_manifest:
            diagnostic = Diagnostic(
                code='project.no_manifest',
                severity=DiagnosticSeverity.WARNING,
                message='No manifest found in project directory',
                target=DiagnosticTarget(kind='project', path=validation.directory.path),
            )
            inspections.append(
                ProjectInspection(
                    directory=directory,
                    state=ProjectState.NO_MANIFEST,
                    inspection_mode=inspection_mode,
                    status=ResultStatus.PARTIAL,
                    diagnostics=(diagnostic,),
                )
            )
            continue

        failed = failed_by_path.get(validation.directory.path)
        if failed is not None:
            diagnostic = Diagnostic(
                code='project.inspection_failed',
                severity=DiagnosticSeverity.ERROR,
                message=failed.error,
                target=DiagnosticTarget(kind='project', path=validation.directory.path),
                cause=failed.error,
            )
            inspections.append(
                ProjectInspection(
                    directory=directory,
                    state=ProjectState.FAILED,
                    inspection_mode=inspection_mode,
                    summary=InspectionSummary(failed_paths=1),
                    status=ResultStatus.FAILED,
                    diagnostics=(diagnostic,),
                    error=failed.error,
                )
            )
            continue

        if manifest_index >= len(manifests):
            diagnostic = Diagnostic(
                code='project.inspection_missing',
                severity=DiagnosticSeverity.ERROR,
                message='Inspection did not return a manifest result',
                target=DiagnosticTarget(kind='project', path=validation.directory.path),
            )
            inspections.append(
                ProjectInspection(
                    directory=directory,
                    state=ProjectState.FAILED,
                    inspection_mode=inspection_mode,
                    summary=InspectionSummary(failed_paths=1),
                    status=ResultStatus.FAILED,
                    diagnostics=(diagnostic,),
                    error='Inspection did not return a manifest result',
                )
            )
            continue

        manifest = manifests[manifest_index]
        manifest_index += 1
        diagnostics = _manifest_diagnostics(report, manifest.index) if report is not None else ()
        follow_up_actions = _manifest_follow_up_actions(report, manifest.index) if report is not None else ()
        summary = inspection_summary((manifest,), ())
        inspections.append(
            ProjectInspection(
                directory=directory,
                state=ProjectState.INSPECTED,
                inspection_mode=inspection_mode,
                manifest=manifest,
                summary=summary,
                status=result_status(summary.failed == 0 and summary.unavailable == 0, diagnostics),
                diagnostics=diagnostics,
                follow_up_actions=follow_up_actions,
            )
        )
    return inspections


def _manifest_diagnostics(report: SyncInspectionReport, manifest_index: int) -> tuple[Diagnostic, ...]:
    """Return report diagnostics scoped to one manifest."""
    return tuple(
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.target is not None
        and (
            diagnostic.target.manifest_index == manifest_index
            or (
                diagnostic.target.action_ref is not None
                and diagnostic.target.action_ref.manifest_index == manifest_index
            )
        )
    )


def _manifest_follow_up_actions(report: SyncInspectionReport, manifest_index: int) -> tuple[FollowUpAction, ...]:
    """Return report Follow-up actions scoped to one manifest."""
    return tuple(
        action
        for action in report.follow_up_actions
        if action.target is not None
        and (
            action.target.manifest_index == manifest_index
            or (action.target.action_ref is not None and action.target.action_ref.manifest_index == manifest_index)
        )
    )


def _project_summary(projects: tuple[ProjectInspection, ...]) -> ProjectInspectionSummary:
    """Aggregate project inspection counts."""
    return ProjectInspectionSummary(
        projects=len(projects),
        missing=sum(1 for project in projects if project.state == ProjectState.MISSING),
        no_manifest=sum(1 for project in projects if project.state == ProjectState.NO_MANIFEST),
        inspected=sum(1 for project in projects if project.state == ProjectState.INSPECTED),
        failed=sum(1 for project in projects if project.state == ProjectState.FAILED),
        actions=sum(project.summary.actions for project in projects),
        needed=sum(project.summary.needed for project in projects),
        satisfied=sum(project.summary.satisfied for project in projects),
        update_available=sum(project.summary.update_available for project in projects),
        unavailable=sum(project.summary.unavailable for project in projects),
        skipped=sum(project.summary.skipped for project in projects),
    )
