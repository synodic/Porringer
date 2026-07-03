"""CLI command implementation for action builder.

Action plan construction.

Builds the list of `SetupAction` objects from a parsed manifest and
resolved plugins.  Also contains the preview/parse entry point that
loads a manifest and returns a `SetupResults` without executing.
"""

import logging
from pathlib import Path

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.plugin_manager import find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectInstaller
from porringer.core.schema import Ecosystem, PackageRef, PluginKind
from porringer.schema import (
    ManifestMetadata,
    SetupAction,
    SetupManifest,
    SetupResults,
    SyncStrategy,
)

from ..manifest import find_manifest
from .discovery import DiscoveredPlugins, discover_all_plugins

logger = logging.getLogger(__name__)


# Execution order for phased setup.
PHASE_ORDER: list[PluginKind] = [
    PluginKind.RUNTIME,
    PluginKind.PACKAGE,
    PluginKind.TOOL,
    PluginKind.PROJECT,
    PluginKind.SCM,
]


# Maps SyncStrategy to the human-readable verb used in action descriptions.
STRATEGY_VERB: dict[SyncStrategy, str] = {
    SyncStrategy.MINIMAL: 'Install',
    SyncStrategy.LATEST: 'Upgrade',
    SyncStrategy.EXACT: 'Ensure',
}


def action_description(
    kind: PluginKind,
    verb: str,
    installer: str | None,
    package: PackageRef | None = None,
    plugin_target: PackageRef | None = None,
    *,
    registered: bool = True,
) -> str:
    """Build a human-readable action description.

    Centralises the ``via <installer>`` / ``(deferred)`` / ``(no plugin)``
    pattern used in both `build_actions` (preview time) and
    `resolve_deferred_actions` (execution time).

    Args:
        kind: The plugin kind.
        verb: Action verb (e.g. ``"Install"``, ``"Upgrade"``).
        installer: Resolved installer name, or ``None`` for deferred.
        package: The target package (may be ``None`` for PROJECT).
        plugin_target: Parent tool for plugin-management actions.
        registered: When *installer* is ``None``, distinguishes
            ``(deferred)`` (registered but unavailable) from
            ``(no plugin)`` (not registered at all).

    Returns:
        Formatted description string.
    """
    if installer:
        suffix = f'via {installer}'
    elif registered:
        suffix = '(deferred)'
    else:
        suffix = '(no plugin)'

    if kind == PluginKind.PROJECT:
        return f'Sync project {suffix}'

    if kind == PluginKind.SCM:
        return f"Clone '{package}' {suffix}" if package else f'Clone {suffix}'

    if plugin_target is not None and package is not None:
        return f"{verb} plugin '{package}' to '{plugin_target}' {suffix}"

    if package is not None:
        return f"{verb} '{package}' {suffix}"

    return f'{verb} {suffix}'


def _get_plugin_cli_command(
    action: SetupAction,
    strategy: SyncStrategy,
    project_environments: dict[str, ProjectInstaller] | None,
) -> list[str]:
    """Return the native CLI command for a plugin-management action.

    Looks up the ``PluginManager`` for the plugin target and returns
    the appropriate install or upgrade command based on the strategy.

    Returns:
        The CLI command, or empty list when no manager is found.
    """
    assert action.plugin_target is not None
    manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if manager is None or action.package is None:
        return []
    if strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
        return manager.plugin_upgrade_command(action.package, include_prereleases=action.include_prereleases)
    return manager.plugin_install_command(action.package, include_prereleases=action.include_prereleases)


def get_cli_command(
    action: SetupAction,
    plugins: DiscoveredPlugins,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
) -> tuple[str, ...]:
    """Gets the CLI command string for an action.

    Args:
        action: The action to get the command for.
        plugins: Discovered plugin container.
        strategy: The sync strategy (determines install vs upgrade command).

    Returns:
        The CLI command as a tuple of strings, or empty tuple if not applicable.
    """
    environments = plugins.environments
    project_environments = plugins.project_environments
    scm_environments = plugins.scm_environments

    cmd: list[str] = []
    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            if action.installer and action.package and action.installer in environments:
                env = environments[action.installer]
                if action.plugin_target is not None:
                    cmd = _get_plugin_cli_command(action, strategy, project_environments)
                elif strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
                    cmd = env.upgrade_command(action.package, include_prereleases=action.include_prereleases)
                else:
                    cmd = env.install_command(action.package, include_prereleases=action.include_prereleases)
        case PluginKind.PROJECT:
            proj_envs = project_environments or {}
            if action.installer and action.installer in proj_envs:
                cmd = proj_envs[action.installer].project_install_command()
        case PluginKind.SCM:
            scm_envs = scm_environments or {}
            if action.installer and action.package and action.installer in scm_envs:
                scm_env = scm_envs[action.installer]
                cmd = scm_env.clone_command(action.package.name, Path('.'))
        case None:
            pass
    return tuple(cmd)


def _log_unresolved(resolver: BackendResolver, kind: PluginKind, ecosystem: Ecosystem) -> None:
    """Log an appropriate message when no installer could be resolved.

    Distinguishes between a completely unregistered ``(kind, ecosystem)``
    pair (ERROR — will never self-resolve) and a registered-but-unavailable
    pair (INFO — may become available after a preceding phase).
    """
    if not resolver.is_registered(kind, ecosystem):
        logger.error(
            "No plugin registered for (%s, '%s'). "
            'Ensure all required plugin packages are installed '
            'with their entry points available to porringer.',
            kind.value,
            ecosystem,
        )
    else:
        logger.info(
            "Plugin(s) %s registered for (%s, '%s') but unavailable; deferring",
            resolver.registered_names(kind, ecosystem),
            kind.value,
            ecosystem,
        )


def _emit_section_actions(
    actions: list[SetupAction],
    kind: PluginKind,
    ecosystem: Ecosystem,
    packages: list,
    installer: str | None,
    verb: str,
    is_registered: bool,
) -> None:
    """Append actions for a single manifest section to *actions*.

    Handles project, SCM and package/tool/runtime kinds.
    """
    if kind == PluginKind.PROJECT:
        actions.append(
            SetupAction(
                description=action_description(kind, verb, installer, registered=is_registered),
                kind=kind,
                ecosystem=ecosystem,
                installer=installer,
            )
        )
        return

    if kind == PluginKind.SCM:
        for package in packages:
            if not package.is_applicable():
                continue
            scm_description = package.description or str(package.name)
            desc = action_description(kind, verb, installer, package=package.name, registered=is_registered)
            actions.append(
                SetupAction(
                    description=desc,
                    kind=kind,
                    ecosystem=ecosystem,
                    installer=installer,
                    package=package.name,
                    package_description=scm_description,
                )
            )
        return

    for package in packages:
        if not package.is_applicable():
            continue
        desc = action_description(kind, verb, installer, package=package.name, registered=is_registered)
        actions.append(
            SetupAction(
                description=desc,
                kind=kind,
                ecosystem=ecosystem,
                installer=installer,
                package=package.name,
                package_description=package.description,
                include_prereleases=package.include_prereleases,
            )
        )

        for plugin_spec in package.plugins:
            actions.append(
                SetupAction(
                    description=action_description(
                        kind,
                        verb,
                        installer,
                        package=plugin_spec.name,
                        plugin_target=package.name,
                        registered=is_registered,
                    ),
                    kind=kind,
                    ecosystem=ecosystem,
                    installer=installer,
                    package=plugin_spec.name,
                    plugin_target=package.name,
                    include_prereleases=plugin_spec.include_prereleases,
                )
            )


def _build_implicit_project_actions(
    plugins: DiscoveredPlugins,
    resolver: BackendResolver,
    preferences: dict[Ecosystem, str],
    *,
    search_from: Path,
) -> list[SetupAction]:
    """Add one implicit project-sync action for each relevant ecosystem."""
    actions: list[SetupAction] = []
    project_environments = plugins.project_environments or {}
    by_ecosystem: dict[Ecosystem, list[str]] = {}
    evidence_by_ecosystem: dict[Ecosystem, list[str]] = {}

    for installer, plugin in sorted(project_environments.items()):
        if not plugin.project_relevance(search_from):
            continue
        ecosystem = plugin.ecosystem()
        by_ecosystem.setdefault(ecosystem, []).append(installer)
        if plugin.project_evidence(search_from):
            evidence_by_ecosystem.setdefault(ecosystem, []).append(installer)

    for ecosystem, candidates in sorted(by_ecosystem.items(), key=lambda item: str(item[0])):
        preferred = preferences.get(ecosystem)
        selected: str | None = None
        if preferred in candidates:
            selected = resolver.resolve(PluginKind.PROJECT, ecosystem)
        else:
            evidence_candidates = evidence_by_ecosystem.get(ecosystem, [])
            if evidence_candidates:
                suitable_evidence = sorted(
                    name for name in evidence_candidates if project_environments[name].query_availability()
                )
                selected = suitable_evidence[0] if suitable_evidence else None
            elif len(candidates) == 1:
                selected = resolver.resolve(PluginKind.PROJECT, ecosystem)
            else:
                logger.warning(
                    "Multiple project plugins are relevant for ecosystem '%s' but none has project-specific "
                    'evidence: %s. Set a preference to choose explicitly.',
                    ecosystem,
                    ', '.join(candidates),
                )
                continue

        if selected is None:
            if resolver.is_registered(PluginKind.PROJECT, ecosystem):
                actions.append(
                    SetupAction(
                        description=action_description(PluginKind.PROJECT, 'Sync', None, registered=True),
                        kind=PluginKind.PROJECT,
                        ecosystem=ecosystem,
                        installer=None,
                    )
                )
            else:
                _log_unresolved(resolver, PluginKind.PROJECT, ecosystem)
            continue

        actions.append(
            SetupAction(
                description=action_description(PluginKind.PROJECT, 'Sync', selected),
                kind=PluginKind.PROJECT,
                ecosystem=ecosystem,
                installer=selected,
            )
        )
    return actions


def build_actions(
    manifest: SetupManifest,
    plugins: DiscoveredPlugins | dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    *,
    search_from: Path | None = None,
) -> list[SetupAction]:
    """Builds the list of actions from a manifest.

    Iterates each kind section in the manifest.  Package/tool/runtime
    entries produce one action per package.  Project entries produce a
    single action per ecosystem.  SCM entries produce one action per
    repository URL.  The `strategy` parameter controls only the
    human-readable description verb; the execution layer decides
    install-vs-upgrade behaviour at runtime.

    Args:
        manifest: The parsed setup manifest.
        plugins: Discovered plugin container, **or** a plain
            ``dict[str, Environment]`` for backward compatibility
            with existing callers / tests.
        strategy: The sync strategy (used for description text).
        search_from: The directory from which project-relevance discovery
            should start.

    Returns:
        List of actions to perform.
    """
    # Accept a plain environments dict for backward compat (tests, etc.)
    if isinstance(plugins, dict):
        plugins = DiscoveredPlugins(environments=plugins, project_environments={}, scm_environments={})

    actions: list[SetupAction] = []

    if search_from is None:
        search_from = Path('.')

    # Only resolve ecosystems actually referenced by this manifest or relevant
    # project plugins so the resolver doesn't warn about unrelated plugins.
    needed_pairs: set[tuple[PluginKind, Ecosystem]] = set()
    for kind, ecosystem, _packages in manifest.iter_sections():
        needed_pairs.add((kind, ecosystem))
    for plugin in (plugins.project_environments or {}).values():
        if plugin.project_relevance(search_from):
            needed_pairs.add((PluginKind.PROJECT, plugin.ecosystem()))

    resolver = BackendResolver(plugins.all_plugins, manifest.preferences, needed_pairs=needed_pairs)

    verb = STRATEGY_VERB[strategy]

    # Iterate each non-project section from the manifest.
    for kind, ecosystem, packages in manifest.iter_sections():
        if kind == PluginKind.PROJECT:
            continue

        installer = resolver.resolve(kind, ecosystem)

        if installer is None:
            _log_unresolved(resolver, kind, ecosystem)

        is_registered = installer is not None or resolver.is_registered(kind, ecosystem)
        _emit_section_actions(actions, kind, ecosystem, packages, installer, verb, is_registered)

    implicit_project_actions = _build_implicit_project_actions(
        plugins,
        resolver,
        dict(manifest.preferences),
        search_from=search_from,
    )
    if implicit_project_actions:
        actions.extend(implicit_project_actions)

    return actions


def _build_preview(
    path: Path,
    strategy: SyncStrategy,
    *,
    use_cache: bool,
    log_label: str,
    plugins: DiscoveredPlugins | None = None,
) -> SetupResults:
    """Shared implementation for :func:`parse_manifest` and :func:`load_manifest`.

    Finds and parses the manifest, optionally uses cached plugin
    discovery to resolve installer names, builds the action list,
    and returns a :class:`SetupResults` preview.

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.
        use_cache: Forwarded to :func:`discover_all_plugins` when
            *plugins* is ``None``.
        log_label: Human-readable label for the log message.
        plugins: Pre-discovered plugins.  When provided, plugin
            discovery is skipped entirely.

    Returns:
        SetupResults containing the action plan.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    logger.info(f'{log_label} from: {path}')

    result = find_manifest(path)
    resolved_plugins = plugins if plugins is not None else discover_all_plugins(use_cache=use_cache)
    actions = build_actions(
        result.manifest,
        resolved_plugins,
        strategy,
        search_from=result.root_directory,
    )
    metadata = ManifestMetadata(
        name=result.manifest.name,
        description=result.manifest.description,
        author=result.manifest.author,
        url=str(result.manifest.url) if result.manifest.url else None,
    )

    return SetupResults(
        actions=actions,
        manifest_path=result.manifest_path,
        root_directory=result.root_directory,
        metadata=metadata,
        preferences=dict(result.manifest.preferences),
    )


def parse_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
    """Parse a manifest and build the action plan without executing.

    The returned `SetupResults.actions` list contains `SetupAction`
    objects with the following fields useful for introspection:

    * `installer` — canonical plugin name (e.g. `"uv"`, `"brew"`).
    * `kind` — `PluginKind` enum (`PACKAGE`, `TOOL`, `RUNTIME`,
            `PROJECT`, `SCM`).
    * `ecosystem` — ecosystem identifier (e.g. `"python"`, `"node"`).
    * `package` — `PackageRef` with name and optional version constraint.

    This is an internal helper for tests and implementation code. Public
    callers should use `api.sync.inspect(...)` for read-only manifest
    information.

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.

    Returns:
        SetupResults containing the list of actions that would be performed.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return _build_preview(path, strategy, use_cache=True, log_label='Parsing manifest')


def load_manifest(
    path: Path,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    *,
    plugins: DiscoveredPlugins | None = None,
) -> SetupResults:
    """Load a manifest using cached plugin knowledge.

    This is the fast path for GUI clients: it reads JSON, builds
    ``SetupAction`` objects using cached plugin knowledge, and
    returns immediately.  On a warm cache the only I/O is the
    manifest file read.  Actions whose ``installer`` cannot be
    resolved from cached plugins will have ``installer=None``
    (deferred) — the execution engine resolves them at phase
    boundaries.

    Use :func:`parse_manifest` when you need a fully-resolved preview
    with populated CLI commands.

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.
        plugins: Pre-discovered plugins. ``None`` uses cached
            discovery internally.

    Returns:
        SetupResults containing the action plan.  Actions with
        unresolvable installers have ``installer=None``.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return _build_preview(path, strategy, use_cache=True, log_label='Loading manifest (fast)', plugins=plugins)
