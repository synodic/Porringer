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
from typing import Any

from packaging.version import Version

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.plugin_schema.environment import CheckUpdatesParameters
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.schema import (
    BatchSetupResults,
    CheckParameters,
    CheckResult,
    ManifestValidationResult,
    PackageUpdateInfo,
    ProgressEvent,
    ProgressEventKind,
    RuntimeCheckResult,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SyncStrategy,
)
from porringer.utility.exception import ManifestError, PluginError, UpdateError

from .core.action_builder import (
    async_load_manifest,
    async_parse_manifest,
    load_manifest,
    parse_manifest,
)
from .core.discovery import discover_all_plugins, invalidate_plugin_cache
from .core.execution import _plugins_discovered_event, execute_single
from .manifest import has_manifest as _has_manifest
from .manifest import manifest_filenames as _manifest_filenames
from .manifest import manifest_schema, validate_manifest

logger = logging.getLogger(__name__)


async def _build_update_infos(
    env: Any,
    check_params: CheckUpdatesParameters,
    runtime_context: RuntimeContext,
) -> list[PackageUpdateInfo]:
    """Query an environment for package updates and return structured info.

    Shared by :meth:`SyncCommands.check_updates` and
    :meth:`SyncCommands.check_updates_by_runtime`.
    """
    installed = await env.packages(runtime_context=runtime_context)
    installed_map = {str(p.name): p for p in installed}

    updates = await env.check_updates(check_params)

    infos: list[PackageUpdateInfo] = []
    for update_pkg in updates:
        current = installed_map.get(str(update_pkg.name))
        current_version = Version(current.version) if current and current.version else None
        latest_version = Version(update_pkg.version) if update_pkg.version else None
        infos.append(
            PackageUpdateInfo(
                name=str(update_pkg.name),
                current_version=current_version,
                latest_version=latest_version,
                update_available=True,
            )
        )
    return infos


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

        .. note::
            Prefer :meth:`async_parse_manifest` in async contexts.
        """
        return parse_manifest(path, strategy)

    @staticmethod
    def load_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
        """Load a manifest quickly using cached plugin discovery.

        Delegates to `action_builder.load_manifest` — the fast path
        for GUI preview.  Actions whose installer cannot be resolved
        from cached plugins will have ``installer=None``.

        .. note::
            Prefer :meth:`async_load_manifest` in async contexts.
        """
        return load_manifest(path, strategy)

    @staticmethod
    async def async_parse_manifest(
        path: Path,
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> SetupResults:
        """Parse a manifest asynchronously.

        Offloads blocking I/O to a thread.  When *plugins* is
        provided, plugin discovery is skipped — use this with a
        pre-discovered ``DiscoveredPlugins`` to avoid redundant
        entry-point scanning.

        Args:
            path: Path to manifest file or directory containing one.
            strategy: The sync strategy.
            plugins: Pre-discovered plugins.

        Returns:
            SetupResults containing the list of actions.
        """
        return await async_parse_manifest(path, strategy, plugins=plugins)

    @staticmethod
    async def async_load_manifest(
        path: Path,
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> SetupResults:
        """Load a manifest asynchronously using cached plugin discovery.

        This is the preferred entry-point for GUI / async callers.
        Offloads blocking I/O to a thread and accepts pre-discovered
        plugins to eliminate redundant discovery.

        Args:
            path: Path to manifest file or directory containing one.
            strategy: The sync strategy.
            plugins: Pre-discovered plugins.

        Returns:
            SetupResults containing the action plan.
        """
        return await async_load_manifest(path, strategy, plugins=plugins)

    # --- Update checking ---

    @staticmethod
    async def check_updates(
        params: CheckParameters | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> list[CheckResult]:
        """Check for package updates across all (or selected) plugins.

        This is the API equivalent of ``porringer check``.  It iterates
        over discovered ``Environment`` plugins, calls
        ``env.check_updates()`` on each, and assembles the results
        into a list of :class:`CheckResult` objects.

        Accepts a pre-discovered :class:`DiscoveredPlugins` to skip
        redundant entry-point scanning and runtime resolution.

        Args:
            params: Optional check parameters (plugin filter,
                pre-release flag).  When ``None``, all plugins are
                checked with default settings.
            plugins: Pre-discovered plugins from
                :meth:`API.discover_plugins`.

        Returns:
            One :class:`CheckResult` per queried plugin, carrying
            per-package update info or an error string.
        """
        if params is None:
            params = CheckParameters()

        # Discover plugins if not pre-passed
        if plugins is None:
            plugins = await asyncio.to_thread(discover_all_plugins, use_cache=True)

        # Resolve runtime context
        runtime_context = plugins.resolved_runtime()
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(plugins.environments)

        results: list[CheckResult] = []

        for name, env in plugins.environments.items():
            # Filter to requested plugins
            if params.plugins and name not in params.plugins:
                continue

            # Skip unsupported or unavailable plugins
            plugin_type = type(env)
            if not plugin_type.is_supported() or not env.query_availability(runtime_context):
                logger.debug('Skipping unavailable plugin %s for update check', name)
                continue

            try:
                check_params = CheckUpdatesParameters(
                    packages=[],
                    include_prereleases=params.include_prereleases,
                    runtime_context=runtime_context,
                )
                package_infos = await _build_update_infos(env, check_params, runtime_context)
                results.append(CheckResult(plugin=name, packages=package_infos))

            except (PluginError, UpdateError) as e:
                logger.error('Plugin error checking updates for %s: %s', name, e)
                results.append(CheckResult(plugin=name, error=str(e)))
            except Exception as e:
                logger.warning('Failed to check updates for %s: %s', name, e)
                results.append(CheckResult(plugin=name, error=str(e)))

        return results

    @staticmethod
    async def check_updates_by_runtime(
        params: CheckParameters | None = None,
        *,
        plugins: DiscoveredPlugins | None = None,
    ) -> list[RuntimeCheckResult]:
        """Check for package updates per installed runtime.

        For each :class:`~porringer.core.plugin_schema.runtime.RuntimeConsumer`
        plugin, resolves **all** available runtime tags via
        :meth:`Builder.resolve_all_runtime_executables
        <porringer.backend.builder.Builder.resolve_all_runtime_executables>`
        and checks each runtime independently.  Non-consumer plugins
        are skipped.

        Availability checks and update queries for each runtime run
        concurrently via :func:`asyncio.gather`.

        Args:
            params: Optional check parameters (plugin filter,
                pre-release flag).  When ``None``, all consumer
                plugins are checked with default settings.
            plugins: Pre-discovered plugins from
                :meth:`API.discover_plugins`.

        Returns:
            A list of :class:`RuntimeCheckResult` entries, one per
            successfully queried runtime, ordered by descending tag
            version.
        """
        if params is None:
            params = CheckParameters()

        if plugins is not None:
            environments = plugins.environments
        else:
            environments = dict((await asyncio.to_thread(discover_all_plugins, use_cache=True)).environments)

        # Resolve every runtime tag across all providers
        all_runtimes = await Builder.resolve_all_runtime_executables(environments)
        if not all_runtimes:
            return []

        # Collect the consumer plugins we need to check
        consumers: dict[str, Any] = {}
        for name, env in environments.items():
            if params.plugins and name not in params.plugins:
                continue
            if not isinstance(env, RuntimeConsumer):
                continue
            if not env.is_supported():
                continue
            consumers[name] = env

        if not consumers:
            return []

        results: list[RuntimeCheckResult] = []

        async def _check_runtime(rt) -> RuntimeCheckResult | None:
            ctx = RuntimeContext(executables={rt.kind: rt.executable})
            check_results: list[CheckResult] = []

            for name, env in consumers.items():
                if env.consumed_runtime_kind() != rt.kind:
                    continue
                if not env.query_availability(ctx):
                    logger.debug(
                        "Plugin '%s' not available for runtime %s (tag=%s)",
                        name,
                        rt.executable,
                        rt.tag,
                    )
                    continue

                try:
                    check_params = CheckUpdatesParameters(
                        packages=[],
                        include_prereleases=params.include_prereleases,
                        runtime_context=ctx,
                    )
                    package_infos = await _build_update_infos(env, check_params, ctx)
                    check_results.append(CheckResult(plugin=name, packages=package_infos))
                except Exception as e:
                    logger.warning(
                        'Failed to check updates for %s on runtime %s: %s',
                        name,
                        rt.tag,
                        e,
                    )
                    check_results.append(CheckResult(plugin=name, error=str(e)))

            if not check_results:
                return None

            return RuntimeCheckResult(
                provider=rt.provider,
                tag=rt.tag,
                executable=rt.executable,
                results=check_results,
            )

        gathered = await asyncio.gather(*[_check_runtime(rt) for rt in all_runtimes])
        for entry in gathered:
            if entry is not None:
                results.append(entry)

        return results

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

            # Filter actions to only included packages
            if parameters.include_packages:
                names = {n.lower() for n in parameters.include_packages}
                preview.actions = [a for a in preview.actions if a.package is None or a.package.name.lower() in names]

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
        *,
        plugins: DiscoveredPlugins | None = None,
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
            plugins: Pre-discovered plugins from
                :meth:`API.discover_plugins`.  When provided, plugin
                discovery is skipped and the ``PLUGINS_DISCOVERED``
                event is **not** emitted (the caller already has the
                availability map).

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

                # Use pre-passed plugins when available; otherwise
                # discover once for the entire batch.
                shared_plugins = plugins
                if shared_plugins is None:
                    if not parameters.dry_run:
                        invalidate_plugin_cache()
                    shared_plugins = await asyncio.to_thread(discover_all_plugins, use_cache=parameters.dry_run)

                    # Emit PLUGINS_DISCOVERED once for the batch — before
                    # any per-manifest work so the GUI gets the availability
                    # map as early as possible.  Skipped when the caller
                    # pre-passed plugins (they already have the map).
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
            # Propagate any exception from _run() so callers see the
            # real error instead of silently receiving an empty stream.
            if task.done() and not task.cancelled():
                task.result()
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

    async def run(self, parameters: SetupParameters, *, plugins: DiscoveredPlugins | None = None) -> BatchSetupResults:
        """Execute setup and return collected results.

        Drains :meth:`execute_stream` internally so that there is exactly
        one execution path.  All ``ProgressEvent`` items are consumed
        and partitioned into a ``BatchSetupResults``.

        Args:
            parameters: The setup parameters (paths, dry_run, strategy, etc.).
            plugins: Pre-discovered plugins (forwarded to
                :meth:`execute_stream`).

        Returns:
            BatchSetupResults from execution.
        """
        manifests: list[SetupResults] = []
        collected: list[SetupActionResult] = []
        failed_paths: list[tuple[Path, str]] = []

        async for event in self.execute_stream(parameters, plugins=plugins):
            if event.kind == ProgressEventKind.MANIFEST_LOADED and event.manifest:
                manifests.append(event.manifest)
            elif event.kind == ProgressEventKind.MANIFEST_FAILED and event.failed_path:
                failed_paths.append(event.failed_path)
            elif event.kind == ProgressEventKind.ACTION_COMPLETED and event.result:
                collected.append(event.result)

        # Partition collected results by manifest based on action identity
        manifest_action_sets = [set(id(a) for a in m.actions) for m in manifests]
        manifest_results: list[SetupResults] = []

        for preview, action_ids in zip(manifests, manifest_action_sets, strict=False):
            mr_results = [r for r in collected if id(r.action) in action_ids]
            sr = SetupResults(
                actions=preview.actions,
                results=mr_results,
                manifest_path=preview.manifest_path,
                root_directory=preview.root_directory,
                metadata=preview.metadata,
                preferences=preview.preferences,
            )
            manifest_results.append(sr)

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)
