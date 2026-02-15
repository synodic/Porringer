"""The sync command module.

Thin facade that wires together manifest loading, action building,
presence detection, and phased execution.  The heavy lifting lives
in the sibling modules:

* `.manifest`       — loading and validating manifests
* `.action_builder` — building the action plan from a manifest
* `.presence`       — dry-run / presence detection
* `.execution`      — phased async execution engine
* `.discovery`      — plugin entry-point discovery
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from porringer.backend.cache import DirectoryCacheManager
from porringer.schema import (
    BatchSetupResults,
    DownloadParameters,
    DownloadResult,
    ManifestValidationResult,
    ProgressCallback,
    ProgressEvent,
    ProgressEventKind,
    SetupParameters,
    SetupResults,
    SyncStrategy,
)
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError

from .action_builder import parse_manifest
from .execution import execute_single
from .manifest import manifest_schema, validate_manifest

logger = logging.getLogger(__name__)


class SyncCommands:
    """Update commands for downloading updates and setting up from manifests."""

    def __init__(self, cache_manager: DirectoryCacheManager | None = None) -> None:
        """Initialize the SyncCommands class.

        Args:
            cache_manager: Optional cache manager for resolving cached paths.
        """
        self._cache_manager = cache_manager

    # --- Static helpers delegated to sub-modules ---

    @staticmethod
    def download(
        parameters: DownloadParameters,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download a file with optional hash verification.

        Args:
            parameters: Download parameters including URL and destination.
            progress_callback: Optional callback for progress updates.

        Returns:
            DownloadResult with success status and details.
        """
        logger.info(f'Downloading: {parameters.url}')
        return download_file(parameters, progress_callback)

    @staticmethod
    def validate_manifest(path: Path) -> ManifestValidationResult:
        """Validate a manifest for errors without executing any operations.

        Delegates to `manifest.validate_manifest`.
        """
        return validate_manifest(path)

    @staticmethod
    def manifest_schema() -> dict:
        """Export a JSON Schema representation of the manifest format.

        Delegates to `manifest.manifest_schema`.
        """
        return manifest_schema()

    @staticmethod
    def parse_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
        """Parse a manifest and build the action plan without executing.

        Delegates to `action_builder.parse_manifest`.
        """
        return parse_manifest(path, strategy)

    # --- Path resolution ---

    def _resolve_paths(self, parameters: SetupParameters) -> list[Path]:
        """Resolve paths from parameters, using cache if needed.

        Args:
            parameters: The setup parameters.

        Returns:
            List of paths to process.

        Raises:
            ValueError: If no paths can be resolved.
        """
        # Explicit paths provided
        if parameters.paths is not None:
            if isinstance(parameters.paths, Path):
                return [parameters.paths]
            return list(parameters.paths)

        # Use cache
        if self._cache_manager is None:
            # Default to current directory if no cache
            return [Path('.')]

        paths = self._cache_manager.get_paths()
        if not paths:
            raise ValueError('No cached directories. Add directories first with "porringer cache add".')

        return paths

    # --- Streaming API ---

    async def execute_stream(
        self,
        parameters: SetupParameters,
    ) -> AsyncIterator[ProgressEvent]:
        """Stream progress events while executing setup actions.

        Resolves paths, parses manifests, and executes (or dry-runs) in a
        single call.  A `ProgressEventKind.MANIFEST_LOADED` event is
        emitted for each successfully parsed manifest before its actions
        begin executing.

        Yields `ProgressEvent` items as manifests are loaded, actions
        start, complete, and report sub-action detail.  Cancellation is
        handled via standard `task.cancel()` on the consuming task.

        Args:
            parameters: The setup parameters (paths, dry_run, strategy, etc.).

        Yields:
            ProgressEvent for each manifest load, action lifecycle transition,
            and sub-action update.
        """
        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()

        async def _run() -> None:
            """Resolve paths, parse manifests, execute, and emit events."""
            try:
                paths = self._resolve_paths(parameters)
                logger.info(f'Executing setup for {len(paths)} path(s) (dry_run={parameters.dry_run})')

                for path in paths:
                    try:
                        preview = parse_manifest(path, strategy=parameters.strategy)
                    except ManifestError as e:
                        logger.warning(f'Failed to load manifest at {path}: {e.error}')
                        queue.put_nowait(
                            ProgressEvent(
                                kind=ProgressEventKind.MANIFEST_FAILED,
                                failed_path=(path, str(e.error)),
                            )
                        )
                        if parameters.fail_fast:
                            break
                        continue

                    # Filter actions to only included plugins
                    if parameters.plugins:
                        preview.actions = [
                            a for a in preview.actions if a.installer is None or a.installer in parameters.plugins
                        ]

                    # Emit MANIFEST_LOADED so consumers know the action plan
                    queue.put_nowait(
                        ProgressEvent(
                            kind=ProgressEventKind.MANIFEST_LOADED,
                            manifest=preview,
                        )
                    )

                    if preview.manifest_path is None:
                        continue

                    await execute_single(
                        preview.actions,
                        preview.manifest_path,
                        parameters,
                        event_queue=queue,
                    )
            finally:
                # Sentinel signals the generator to stop
                queue.put_nowait(None)

        task = asyncio.ensure_future(_run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def run(self, parameters: SetupParameters) -> BatchSetupResults:
        """Execute setup synchronously and return collected results.

        Resolves paths and parses manifests synchronously, then executes
        (or dry-runs) actions via `execute_stream()` for each manifest.

        Args:
            parameters: The setup parameters (paths, dry_run, strategy, etc.).

        Returns:
            BatchSetupResults from execution.
        """
        paths = self._resolve_paths(parameters)
        logger.info(f'Running setup for {len(paths)} path(s) (dry_run={parameters.dry_run})')

        manifest_results: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = []
        previews: list[SetupResults] = []

        # Phase 1: synchronous manifest loading
        for path in paths:
            try:
                preview = parse_manifest(path, strategy=parameters.strategy)

                # Filter actions to only included plugins
                if parameters.plugins:
                    preview.actions = [
                        a for a in preview.actions if a.installer is None or a.installer in parameters.plugins
                    ]

                previews.append(preview)
            except ManifestError as e:
                failed_paths.append((path, str(e.error)))
                if parameters.fail_fast:
                    break

        # Phase 2: async execution for successfully loaded manifests
        if previews:

            async def _execute_all() -> None:
                for preview in previews:
                    if preview.manifest_path is None:
                        continue
                    sr = await execute_single(preview.actions, preview.manifest_path, parameters)
                    sr.manifest_path = preview.manifest_path
                    sr.metadata = preview.metadata
                    manifest_results.append(sr)

            asyncio.run(_execute_all())

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)
