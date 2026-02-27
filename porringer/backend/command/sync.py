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

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from porringer.backend.cache import DirectoryCacheManager
from porringer.schema import (
    BatchSetupResults,
    ManifestValidationResult,
    ProgressEvent,
    ProgressEventKind,
    SetupParameters,
    SetupResults,
    SyncStrategy,
)
from porringer.utility.exception import ManifestError

from .core.action_builder import load_manifest, parse_manifest
from .core.discovery import discover_all_plugins, invalidate_plugin_cache
from .core.execution import _plugins_discovered_event, execute_single
from .manifest import has_manifest as _has_manifest
from .manifest import manifest_filenames as _manifest_filenames
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
    def manifest_filenames() -> tuple[str, ...]:
        """Return all recognised manifest filenames, native first.

        The first element is always ``'porringer.json'``.  Subsequent
        entries are contributed dynamically by installed project plugins
        via the ``ManifestContributor`` protocol.

        GUI clients use this to build file-dialog filters without
        hardcoding filenames.

        Returns:
            Ordered tuple of filenames the discovery engine will probe.
        """
        return _manifest_filenames()

    @staticmethod
    def has_manifest(path: Path) -> bool:
        """Check whether a path resolves to a valid porringer manifest.

        A lightweight existence + parsability check without deep
        validation (no plugin resolution, no PEP 440 checks).

        Args:
            path: Path to a manifest file or directory containing one.

        Returns:
            ``True`` if a manifest can be found and loaded.
        """
        return _has_manifest(path)

    @staticmethod
    def parse_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
        """Parse a manifest and build the action plan without executing.

        Delegates to `action_builder.parse_manifest`.
        """
        return parse_manifest(path, strategy)

    @staticmethod
    def load_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
        """Load a manifest quickly using cached plugin discovery.

        Delegates to `action_builder.load_manifest` — the fast path
        for GUI preview.  Actions whose installer cannot be resolved
        from cached plugins will have ``installer=None``.
        """
        return load_manifest(path, strategy)

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
        if parameters.paths is not None:
            if isinstance(parameters.paths, Path):
                return [parameters.paths]
            return list(parameters.paths)

        if self._cache_manager is None:
            return [Path('.')]

        paths = self._cache_manager.get_paths()
        if not paths:
            raise ValueError('No cached directories. Add directories first with "porringer cache add".')

        return paths

    # --- Manifest loading ---

    def _load_manifests(self, parameters: SetupParameters) -> tuple[list[SetupResults], list[tuple[Path, str]]]:
        """Load and filter manifests from the resolved paths.

        Shared by `run()` and `execute_stream()` to avoid duplicating
        the parse → filter → error-handling loop.

        Args:
            parameters: The setup parameters.

        Returns:
            A tuple of (loaded previews, failed paths).
        """
        paths = self._resolve_paths(parameters)
        logger.info(f'Processing {len(paths)} path(s) (dry_run={parameters.dry_run})')

        previews: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = []

        for path in paths:
            try:
                preview = load_manifest(path, strategy=parameters.strategy)
            except ManifestError as e:
                logger.warning(f'Failed to load manifest at {path}: {e.error}')
                failed_paths.append((path, str(e.error)))
                if parameters.fail_fast:
                    break
                continue

            # Filter actions to only included plugins
            if parameters.plugins:
                preview.actions = [
                    a for a in preview.actions if a.installer is None or a.installer in parameters.plugins
                ]

            # Apply caller-level prerelease overrides
            if parameters.prerelease_packages:
                overrides = {n.lower() for n in parameters.prerelease_packages}
                for action in preview.actions:
                    if action.package is not None and action.package.name.lower() in overrides:
                        action.include_prereleases = True

            previews.append(preview)

        return previews, failed_paths

    # --- Streaming API ---

    async def execute_stream(
        self,
        parameters: SetupParameters,
    ) -> AsyncIterator[ProgressEvent]:
        """Stream progress events while executing setup actions.

        Resolves paths, parses manifests, and executes (or dry-runs) in a
        single call.  Events are emitted in three stages:

        1. ``MANIFEST_PARSED`` — emitted immediately after the manifest
           JSON is loaded and actions are built.  GUI clients can use
           this to populate cards before dry-run checks begin.
        2. ``MANIFEST_LOADED`` — emitted after plugin discovery
           completes and CLI commands are populated on each action.
           This is the fully-resolved preview.
        3. ``ACTION_STARTED`` / ``ACTION_COMPLETED`` /
           ``SUB_ACTION_PROGRESS`` — per-action lifecycle events during
           dry-run or real execution.

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
            """Load manifests, emit events, and execute."""
            try:
                previews, failed = await asyncio.to_thread(self._load_manifests, parameters)

                for path, error in failed:
                    queue.put_nowait(
                        ProgressEvent(
                            kind=ProgressEventKind.MANIFEST_FAILED,
                            failed_path=(path, error),
                        )
                    )

                for preview in previews:
                    # Stage 1: fast preview — cards can be shown immediately
                    queue.put_nowait(
                        ProgressEvent(
                            kind=ProgressEventKind.MANIFEST_PARSED,
                            manifest=preview,
                        )
                    )

                # Pre-discover plugins once for the entire batch.
                # For real execution, invalidate first; for dry-run use
                # the cache to avoid redundant entry-point scanning.
                if not parameters.dry_run:
                    invalidate_plugin_cache()
                shared_plugins = await asyncio.to_thread(discover_all_plugins, use_cache=parameters.dry_run)

                # Emit PLUGINS_DISCOVERED once for the batch — before
                # any per-manifest work so the GUI gets the availability
                # map as early as possible.
                queue.put_nowait(await asyncio.to_thread(_plugins_discovered_event, shared_plugins))

                for preview in previews:
                    # Stage 2 + 3: execute_single populates CLI commands,
                    # emits MANIFEST_LOADED, then streams ACTION_* events.
                    await execute_single(
                        preview,
                        parameters,
                        event_queue=queue,
                        plugins=shared_plugins,
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

        Args:
            parameters: The setup parameters (paths, dry_run, strategy, etc.).

        Returns:
            BatchSetupResults from execution.
        """
        previews, failed_paths = self._load_manifests(parameters)

        manifest_results: list[SetupResults] = []
        if previews:

            async def _execute_all() -> None:
                if not parameters.dry_run:
                    invalidate_plugin_cache()
                shared_plugins = discover_all_plugins(use_cache=parameters.dry_run)
                for preview in previews:
                    sr = await execute_single(preview, parameters, plugins=shared_plugins)
                    manifest_results.append(sr)

            asyncio.run(_execute_all())

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)
