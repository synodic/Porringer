"""Action plan construction.

Builds the list of `SetupAction` objects from a parsed manifest and
resolved plugins.  Also contains the preview/parse entry point that
loads a manifest and returns a `SetupResults` without executing.
"""

import asyncio
import logging
import shlex
from pathlib import Path

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.plugin_manager import find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
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


# Execution order for phased setup.  `None` represents post-sync commands.
PHASE_ORDER: list[PluginKind | None] = [
    PluginKind.RUNTIME,
    PluginKind.PACKAGE,
    PluginKind.TOOL,
    PluginKind.PROJECT,
    PluginKind.SCM,
    None,
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
    project_environments: dict[str, ProjectEnvironment] | None,
) -> list[str]:
    """Return the native CLI command for a plugin-management action.

    Looks up the ``PluginManager`` for the plugin target and returns
    the appropriate add or update command based on the strategy.

    Returns:
        The CLI command, or empty list when no manager is found.
    """
    assert action.plugin_target is not None
    manager = find_plugin_manager(action.plugin_target.name, project_environments)
    if manager is None or action.package is None:
        return []
    if strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
        return manager.plugin_update_command(action.package, include_prereleases=action.include_prereleases)
    return manager.plugin_add_command(action.package, include_prereleases=action.include_prereleases)


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
                cmd = proj_envs[action.installer].sync_command()
        case PluginKind.SCM:
            scm_envs = scm_environments or {}
            if action.installer and action.package and action.installer in scm_envs:
                scm_env = scm_envs[action.installer]
                cmd = scm_env.clone_command(action.package.name, Path('.'))
        case None:
            return action.command or ()
    return tuple(cmd)


def get_uninstall_cli_command(
    action: SetupAction,
    plugins: DiscoveredPlugins,
) -> list[str]:
    """Gets the CLI command for an uninstall action.

    Args:
        action: The action to get the uninstall command for.
        plugins: Discovered plugin container.

    Returns:
        The CLI command as a list of strings, or empty list if not applicable.
    """
    environments = plugins.environments
    project_environments = plugins.project_environments

    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            if action.installer and action.package and action.installer in environments:
                env = environments[action.installer]
                if action.plugin_target is not None:
                    manager = find_plugin_manager(action.plugin_target.name, project_environments)
                    if manager is not None and action.package is not None:
                        return manager.plugin_remove_command(action.package)
                    return []
                return env.uninstall_command(action.package)
        case _:
            pass
    return []


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
    *,
    distro: str | None = None,
) -> None:
    """Append actions for a single manifest section to *actions*.

    Handles project, SCM and package/tool/runtime kinds.
    When *distro* is set the description is prefixed with ``[WSL:<distro>]``.
    """
    prefix = f'[WSL:{distro}] ' if distro else ''

    if kind == PluginKind.PROJECT:
        actions.append(
            SetupAction(
                description=prefix + action_description(kind, verb, installer, registered=is_registered),
                kind=kind,
                ecosystem=ecosystem,
                installer=installer,
                distro=distro,
            )
        )
        return

    if kind == PluginKind.SCM:
        for package in packages:
            if not package.is_applicable():
                continue
            scm_description = package.description or str(package.name)
            desc = prefix + action_description(kind, verb, installer, package=package.name, registered=is_registered)
            actions.append(
                SetupAction(
                    description=desc,
                    kind=kind,
                    ecosystem=ecosystem,
                    installer=installer,
                    package=package.name,
                    package_description=scm_description,
                    distro=distro,
                )
            )
        return

    for package in packages:
        if not package.is_applicable():
            continue
        desc = prefix + action_description(kind, verb, installer, package=package.name, registered=is_registered)
        actions.append(
            SetupAction(
                description=desc,
                kind=kind,
                ecosystem=ecosystem,
                installer=installer,
                package=package.name,
                package_description=package.description,
                include_prereleases=package.include_prereleases,
                distro=distro,
            )
        )

        for plugin_spec in package.plugins:
            actions.append(
                SetupAction(
                    description=prefix
                    + action_description(
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
                    distro=distro,
                )
            )


def build_actions(
    manifest: SetupManifest,
    plugins: DiscoveredPlugins | dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
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

    Returns:
        List of actions to perform.
    """
    # Accept a plain environments dict for backward compat (tests, etc.)
    if isinstance(plugins, dict):
        plugins = DiscoveredPlugins(environments=plugins, project_environments={}, scm_environments={})

    actions: list[SetupAction] = []

    # Only resolve ecosystems actually referenced by this manifest so
    # the resolver doesn't warn about irrelevant registered plugins.
    needed_pairs: set[tuple[PluginKind, Ecosystem]] = set()
    for kind, ecosystem, _packages in manifest.iter_sections():
        needed_pairs.add((kind, ecosystem))

    resolver = BackendResolver(plugins.all_plugins, manifest.preferences, needed_pairs=needed_pairs)

    verb = STRATEGY_VERB[strategy]

    # Iterate each kind section
    for kind, ecosystem, packages in manifest.iter_sections():
        installer = resolver.resolve(kind, ecosystem)

        if installer is None:
            _log_unresolved(resolver, kind, ecosystem)

        is_registered = installer is not None or resolver.is_registered(kind, ecosystem)
        _emit_section_actions(actions, kind, ecosystem, packages, installer, verb, is_registered)

    # ---- WSL2 distro sections -------------------------------------------
    for distro, distro_manifest in manifest.wsl2.items():
        wsl_needed: set[tuple[PluginKind, Ecosystem]] = set()
        for kind, ecosystem, _packages in distro_manifest.iter_sections():
            wsl_needed.add((kind, ecosystem))

        wsl_resolver = BackendResolver(
            plugins.all_plugins,
            distro_manifest.preferences,
            needed_pairs=wsl_needed,
        )

        for kind, ecosystem, packages in distro_manifest.iter_sections():
            installer = wsl_resolver.resolve(kind, ecosystem)
            if installer is None:
                _log_unresolved(wsl_resolver, kind, ecosystem)

            is_registered = installer is not None or wsl_resolver.is_registered(kind, ecosystem)
            _emit_section_actions(actions, kind, ecosystem, packages, installer, verb, is_registered, distro=distro)

    # Add post-sync command actions (kind=None)
    for command_str in manifest.post_sync:
        command_parts = tuple(shlex.split(command_str))
        actions.append(
            SetupAction(
                description=f'Run: {command_str}',
                command=command_parts,
            )
        )

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


async def async_load_manifest(
    path: Path,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    *,
    plugins: DiscoveredPlugins | None = None,
) -> SetupResults:
    """Async version of :func:`load_manifest`.

    Offloads blocking manifest I/O and plugin discovery to a thread.
    When *plugins* is provided, discovery is skipped and only the
    manifest file read is threaded.

    This is the preferred entry-point for async callers (GUI, API).

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.
        plugins: Pre-discovered plugins.  ``None`` triggers cached
            discovery internally.

    Returns:
        SetupResults containing the action plan.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return await asyncio.to_thread(
        _build_preview, path, strategy, use_cache=True, log_label='Loading manifest (fast)', plugins=plugins
    )


async def async_parse_manifest(
    path: Path,
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    *,
    plugins: DiscoveredPlugins | None = None,
) -> SetupResults:
    """Async version of :func:`parse_manifest`.

    Offloads blocking manifest I/O and plugin discovery to a thread.

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.
        plugins: Pre-discovered plugins.

    Returns:
        SetupResults containing the list of actions that would be performed.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return await asyncio.to_thread(
        _build_preview, path, strategy, use_cache=True, log_label='Parsing manifest', plugins=plugins
    )


def parse_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
    """Parse a manifest and build the action plan without executing.

    The returned `SetupResults.actions` list contains `SetupAction`
    objects with the following fields useful for introspection:

    * `installer` — canonical plugin name (e.g. `"uv"`, `"brew"`).
    * `kind` — `PluginKind` enum (`PACKAGE`, `TOOL`, `RUNTIME`,
      `PROJECT`, `SCM`) or `None` for post-sync commands.
    * `ecosystem` — ecosystem identifier (e.g. `"python"`, `"node"`).
    * `package` — `PackageRef` with name and optional version constraint.

    This method is also available as a module-level convenience:
    `porringer.parse_manifest(path)`.

    Args:
        path: Path to manifest file or directory containing one.
        strategy: The sync strategy.

    Returns:
        SetupResults containing the list of actions that would be performed.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return _build_preview(path, strategy, use_cache=True, log_label='Parsing manifest')


def load_manifest(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
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

    Returns:
        SetupResults containing the action plan.  Actions with
        unresolvable installers have ``installer=None``.

    Raises:
        ManifestError: If the manifest cannot be found or parsed.
    """
    return _build_preview(path, strategy, use_cache=True, log_label='Loading manifest (fast)')
