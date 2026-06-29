"""CLI command implementation for sync."""

"""The sync command module.

Thin facade that wires together manifest loading, action building,
presence detection, and phased execution.  The heavy lifting lives
in the sibling modules:

* `.manifest`       — loading and validating manifests
* `.action_builder` — building the action plan from a manifest
* `.presence`       — inspection / presence detection
* `.execution`      — phased async execution engine
* `.discovery`      — plugin entry-point discovery
"""

import asyncio
import contextlib
import inspect as inspectlib
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.plugin_schema.runtime import RuntimeConsumer
from porringer.core.schema import PluginKind
from porringer.schema import (
    ActionCompletedEvent,
    ActionRef,
    BatchSetupResults,
    DownloadParameters,
    DownloadResult,
    FailedPathInspection,
    InspectionMode,
    ManifestFailedEvent,
    ManifestLoadedEvent,
    ManifestValidationResult,
    ProgressEvent,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SyncInspectionReport,
    SyncRunReport,
    action_id_for,
)
from porringer.utility import HTTP_TIMEOUT
from porringer.utility.concurrency import gather_bounded
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError
from porringer.utility.observability import batch_diagnostics, batch_follow_up_actions, result_status

from .core.action_builder import load_manifest
from .core.discovery import discover_all_plugins, invalidate_plugin_cache
from .core.execution import _plugins_discovered_event, execute_single
from .core.inspection import build_manifest_inspection, build_sync_inspection_report
from .manifest import has_manifest as _has_manifest
from .manifest import manifest_filenames as _manifest_filenames
from .manifest import manifest_schema, validate_manifest

logger = logging.getLogger(__name__)


def _action_indices(preview: SetupResults) -> list[int]:
    """Return source action indexes for a preview, initializing when absent."""
    if len(preview.action_indices) != len(preview.actions):
        preview.action_indices = list(range(len(preview.actions)))
    return preview.action_indices


def _inspection_needs_runtime(previews: Sequence[SetupResults], plugins: DiscoveredPlugins) -> bool:
    """Return whether complete inspection needs a resolved runtime context."""
    for preview in previews:
        for action in preview.actions:
            if action.kind is None or action.installer is None:
                continue
            plugin = None
            match action.kind:
                case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
                    plugin = plugins.environments.get(action.installer)
                case PluginKind.PROJECT:
                    plugin = plugins.project_environments.get(action.installer)
                case PluginKind.SCM:
                    plugin = plugins.scm_environments.get(action.installer)
            if isinstance(plugin, RuntimeConsumer):
                return True
    return False


class SyncCommands:
    """Manifest-driven sync commands.

    Handles manifest loading, validation, evented execution, and
    manifest schema export.  Update checking for managed packages
    has moved to :class:`~porringer.backend.command.package.PackageCommands`.
    """

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

    # --- Path resolution ---

    @staticmethod
    def _partition_paths(
        paths: Path | Sequence[str | Path],
    ) -> tuple[list[Path], list[str]]:
        """Split *paths* into local filesystem paths and remote URLs.

        Returns:
            ``(local, urls)`` — local paths and URL strings.
        """
        if isinstance(paths, Path):
            return [paths], []
        local: list[Path] = []
        urls: list[str] = []
        for p in paths:
            if isinstance(p, str):
                parsed = urlparse(p)
                if parsed.scheme in {'http', 'https'}:
                    urls.append(p)
                    continue
                local.append(Path(p))
            else:
                local.append(p)
        return local, urls

    def _resolve_paths(self, parameters: SetupParameters) -> tuple[list[Path], list[str]]:
        """Resolve paths from parameters, using cache if needed.

        Returns:
            ``(local_paths, urls)`` — local paths to process and URLs
            to download.

        Raises:
            ValueError: If no paths can be resolved.
        """
        if parameters.paths is not None:
            return self._partition_paths(parameters.paths)

        if self._cache_manager is None:
            return [Path('.')], []

        paths = self._cache_manager.get_paths()
        if not paths:
            raise ValueError('No cached directories. Add directories first with "porringer cache add".')

        return paths, []

    @staticmethod
    async def _download_urls(
        urls: list[str],
        tmp_dir: Path,
        *,
        max_concurrency: int = 8,
    ) -> list[Path]:
        """Download remote manifest URLs into *tmp_dir*.

        Downloads run with bounded concurrency over a single shared
        :class:`aiohttp.ClientSession` so connections are pooled and TLS
        setup is not repeated per URL.

        Returns a list of local paths to the downloaded files, ordered to
        match *urls*.
        """
        if not urls:
            return []

        dests = [tmp_dir / f'manifest_{i}.json' for i in range(len(urls))]

        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:

            def _make_download(url: str, dest: Path) -> Callable[[], Awaitable[tuple[str, DownloadResult]]]:
                async def _run() -> tuple[str, DownloadResult]:
                    result = await download_file(
                        DownloadParameters(url=url, destination=dest),
                        http_client=session,
                    )
                    return url, result

                return _run

            results = await gather_bounded(
                (_make_download(url, dest) for url, dest in zip(urls, dests, strict=True)),
                limit=max_concurrency,
            )

        for url, result in results:
            if not result.success:
                raise ValueError(f'Failed to download manifest from {url}: {result.message}')
        return dests

    # --- Manifest loading ---

    def _load_manifests(
        self,
        parameters: SetupParameters,
        plugins: DiscoveredPlugins | None = None,
    ) -> tuple[list[SetupResults], list[tuple[int, Path, str]]]:
        """Load and filter manifests from the resolved paths.

        Shared by inspection and execution to avoid duplicating
        the parse → filter → error-handling loop.

        Args:
            parameters: The setup parameters.
            plugins: Optional pre-discovered plugin registry to reuse while
                building manifests.

        Returns:
            A tuple of (loaded previews, failed paths).
        """
        paths, _urls = self._resolve_paths(parameters)
        logger.info('Processing %d path(s)', len(paths))

        previews: list[SetupResults] = []
        failed_paths: list[tuple[int, Path, str]] = []

        for manifest_index, path in enumerate(paths):
            try:
                preview = load_manifest(path, strategy=parameters.strategy, plugins=plugins)
            except ManifestError as e:
                logger.warning(f'Failed to load manifest at {path}: {e.error}')
                failed_paths.append((manifest_index, path, e.error))
                if parameters.fail_fast:
                    break
                continue

            preview.manifest_index = manifest_index
            self._apply_action_filters(preview, parameters, manifest_index=manifest_index)

            previews.append(preview)

        return previews, failed_paths

    @staticmethod
    def _apply_action_filters(preview: SetupResults, parameters: SetupParameters, *, manifest_index: int = 0) -> None:
        """Apply caller-level action filters to a loaded manifest preview."""
        indexed_actions = list(zip(_action_indices(preview), preview.actions, strict=True))

        # Filter actions to only included plugins.
        if parameters.plugins:
            indexed_actions = [
                (index, action)
                for index, action in indexed_actions
                if action.installer is None or action.installer in parameters.plugins
            ]

        # Filter actions to only included packages.
        if parameters.include_packages:
            names = {name.lower() for name in parameters.include_packages}
            indexed_actions = [
                (index, action)
                for index, action in indexed_actions
                if action.package is None or action.package.name.lower() in names
            ]

        # Apply caller-level prerelease overrides.
        if parameters.prerelease_packages:
            overrides = {name.lower() for name in parameters.prerelease_packages}
            indexed_actions = [
                (index, replace(action, include_prereleases=True))
                if action.package is not None and action.package.name.lower() in overrides
                else (index, action)
                for index, action in indexed_actions
            ]

        if parameters.action_ids:
            indexed_actions = [
                (index, action)
                for index, action in indexed_actions
                if action_id_for(manifest_index, index) in parameters.action_ids
            ]

        preview.action_indices = [index for index, _action in indexed_actions]
        preview.actions = [action for _index, action in indexed_actions]

    async def inspect(
        self,
        parameters: SetupParameters,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> SyncInspectionReport:
        """Inspect manifests without executing setup actions.

        This is the structured preview/diagnostics path for frontends
        and CLI JSON output.  It loads manifests, discovers plugins,
        validates each manifest, resolves native commands, performs
        presence/update checks, and returns a stable report.

        Args:
            parameters: Setup parameters controlling paths, strategy,
                filters, prerelease overrides, and project-directory
                behavior.
            plugins: Pre-discovered plugins.  When provided, plugin
                discovery is skipped and the supplied runtime context is
                reused.

        Returns:
            A structured inspection report.
        """
        tmp_dir: Path | None = None
        try:
            local_paths, urls = self._resolve_paths(parameters)
            effective_params = parameters
            if urls:
                tmp = tempfile.mkdtemp(prefix='porringer_')
                tmp_dir = Path(tmp)
                url_paths = await self._download_urls(urls, tmp_dir, max_concurrency=parameters.max_concurrency)
                effective_params = parameters.model_copy(update={'paths': [*local_paths, *url_paths]})

            shared_plugins = plugins
            if shared_plugins is None:
                shared_plugins = await asyncio.to_thread(discover_all_plugins, use_cache=True)

            previews, failed = await asyncio.to_thread(self._load_manifests, effective_params, shared_plugins)
            if (
                effective_params.inspection_mode != InspectionMode.FAST
                and shared_plugins.runtime_context is None
                and _inspection_needs_runtime(previews, shared_plugins)
            ):
                shared_plugins.runtime_context = await Builder.resolve_runtime_context(shared_plugins.environments)

            failed_paths = tuple(
                FailedPathInspection(path=path, error=error, manifest_index=manifest_index)
                for manifest_index, path, error in failed
            )
            manifests = []
            for index, preview in enumerate(previews):
                manifest_index = preview.manifest_index if preview.manifest_index is not None else index
                diagnostics = ()
                if preview.manifest_path is not None:
                    validation = await asyncio.to_thread(validate_manifest, preview.manifest_path)
                    diagnostics = tuple(validation.diagnostics)
                manifests.append(
                    await build_manifest_inspection(
                        index=manifest_index,
                        preview=preview,
                        plugins=shared_plugins,
                        parameters=effective_params,
                        diagnostics=diagnostics,
                    )
                )

            return build_sync_inspection_report(
                manifests=tuple(manifests),
                failed_paths=failed_paths,
                plugins=shared_plugins,
                inspection_mode=effective_params.inspection_mode,
            )
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- Execution API ---

    async def _execution_events(
        self,
        parameters: SetupParameters,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        """Yield progress events while executing setup actions.

        Public callers should use :meth:`run` with ``on_event`` instead
        of depending on the generator implementation directly.
        """
        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        tmp_dir: Path | None = None

        async def _run() -> None:
            """Load manifests, emit events, and execute."""
            nonlocal tmp_dir
            try:
                # Download remote URLs to a temp directory, then merge
                # the downloaded paths into the parameters for manifest
                # loading.
                local_paths, urls = self._resolve_paths(parameters)
                effective_params = parameters
                if urls:
                    tmp = tempfile.mkdtemp(prefix='porringer_')
                    tmp_dir = Path(tmp)
                    url_paths = await self._download_urls(urls, tmp_dir, max_concurrency=parameters.max_concurrency)
                    all_paths = local_paths + url_paths
                    effective_params = parameters.model_copy(update={'paths': all_paths})

                shared_plugins = plugins
                if shared_plugins is None:
                    invalidate_plugin_cache()
                    shared_plugins = await asyncio.to_thread(discover_all_plugins)

                    # Emit PLUGINS_DISCOVERED once for the batch — before
                    # any per-manifest work so the GUI gets the availability
                    # map as early as possible.  Skipped when the caller
                    # pre-passed plugins (they already have the map).
                    queue.put_nowait(await asyncio.to_thread(_plugins_discovered_event, shared_plugins))

                previews, failed = await asyncio.to_thread(self._load_manifests, effective_params, shared_plugins)

                for manifest_index, path, error in failed:
                    queue.put_nowait(ManifestFailedEvent(failed_path=(path, error), manifest_index=manifest_index))

                for index, preview in enumerate(previews):
                    manifest_index = preview.manifest_index if preview.manifest_index is not None else index
                    # execute_single populates CLI commands, emits
                    # MANIFEST_LOADED, then reports ACTION_* events.
                    await execute_single(
                        preview,
                        parameters,
                        event_queue=queue,
                        plugins=shared_plugins,
                        manifest_index=manifest_index,
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
        except GeneratorExit:
            # Consumer closed the async generator (e.g. ``break`` or
            # ``aclose()``).  Cancel synchronously only — ``await``
            # inside a ``GeneratorExit`` handler causes
            # ``RuntimeError: async generator ignored GeneratorExit``.
            if not task.done():
                task.cancel()
            return
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # Propagate any exception from _run() so callers see the
        # real error instead of silently receiving no events.
        if task.done() and not task.cancelled():
            task.result()

    async def run(
        self,
        parameters: SetupParameters,
        *,
        plugins: DiscoveredPlugins | None = None,
        on_event: Callable[[ProgressEvent], object] | None = None,
    ) -> SyncRunReport:
        """Execute setup, optionally observe events, and return a report.

        This is the single public execution entry point.  All progress
        events flow through ``on_event`` when provided, then the same
        events are collected into ``BatchSetupResults``.

        .. warning::
            Execution mutates process-global ``PATH`` so that runtimes
            installed mid-sync become discoverable.  Do **not** run two
            syncs concurrently in the same process; their ``PATH``
            changes are not isolated from one another.  Embedders that
            need parallel syncs should use separate processes.

        Args:
            parameters: The setup parameters (paths, strategy, filters, etc.).
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            on_event: Optional callback invoked for every progress event
                before collection. If it returns an awaitable, it is awaited.

        Returns:
            Structured sync run report with batch results, diagnostics, and follow-up actions.
        """
        manifests: list[SetupResults] = []
        collected: list[tuple[ActionRef | None, SetupActionResult]] = []
        failed_paths: list[tuple[Path, str]] = []

        async for event in self._execution_events(parameters, plugins=plugins):
            if on_event is not None:
                maybe_awaitable = on_event(event)
                if inspectlib.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            if isinstance(event, ManifestLoadedEvent):
                manifests.append(event.manifest)
            elif isinstance(event, ManifestFailedEvent):
                failed_paths.append(event.failed_path)
            elif isinstance(event, ActionCompletedEvent):
                collected.append((event.action_ref, event.result))

        # Partition by stable action refs, keeping object-identity only as
        # a compatibility fallback for legacy events without refs. Results are
        # grouped in a single pass to avoid an O(manifests * results) scan.
        indexed_previews = [
            (preview, preview.manifest_index if preview.manifest_index is not None else index)
            for index, preview in enumerate(manifests)
        ]

        # Map each action's identity to its owning manifest index, used only
        # for legacy ref-less completion events.
        action_to_manifest_index: dict[int, int] = {
            id(action): manifest_index for preview, manifest_index in indexed_previews for action in preview.actions
        }

        results_by_manifest_index: dict[int, list[SetupActionResult]] = {}
        for ref, result in collected:
            target_index = ref.manifest_index if ref is not None else action_to_manifest_index.get(id(result.action))
            if target_index is None:
                continue
            results_by_manifest_index.setdefault(target_index, []).append(result)

        manifest_results: list[SetupResults] = []

        for preview, manifest_index in indexed_previews:
            mr_results = results_by_manifest_index.get(manifest_index, [])
            sr = SetupResults(
                actions=preview.actions,
                action_indices=preview.action_indices,
                results=mr_results,
                manifest_index=manifest_index,
                manifest_path=preview.manifest_path,
                root_directory=preview.root_directory,
                metadata=preview.metadata,
                preferences=preview.preferences,
            )
            manifest_results.append(sr)

        results = BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)
        diagnostics = batch_diagnostics(results)
        return SyncRunReport(
            status=result_status(results.success, diagnostics),
            results=results,
            diagnostics=diagnostics,
            follow_up_actions=batch_follow_up_actions(results),
        )
