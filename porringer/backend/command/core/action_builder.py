"""Action plan construction.

Builds the list of `SetupAction` objects from a parsed manifest and
resolved plugins.  Also contains the preview/parse entry point that
loads a manifest and returns a `SetupResults` without executing.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.plugin_manager import find_plugin_manager
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import PackageRef, PluginKind
from porringer.schema import (
    ManifestMetadata,
    SetupAction,
    SetupManifest,
    SetupResults,
    SyncStrategy,
)

from ..manifest import find_manifest
from .discovery import discover_all_plugins

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
) -> str:
    """Build a human-readable action description.

    Centralises the ``via <installer>`` / ``(deferred)`` pattern used
    in both `build_actions` (preview time) and `_resolve_deferred_actions`
    (execution time).

    Args:
        kind: The plugin kind.
        verb: Action verb (e.g. ``"Install"``, ``"Upgrade"``).
        installer: Resolved installer name, or ``None`` for deferred.
        package: The target package (may be ``None`` for PROJECT).
        plugin_target: Parent tool for plugin-management actions.

    Returns:
        Formatted description string.
    """
    suffix = f'via {installer}' if installer else '(deferred)'

    if kind == PluginKind.PROJECT:
        return f'Sync project {suffix}'

    if kind == PluginKind.SCM:
        return f"Clone '{package}' {suffix}" if package else f'Clone {suffix}'

    if plugin_target is not None and package is not None:
        return f"Add plugin '{package}' to '{plugin_target}' {suffix}"

    if package is not None:
        return f"{verb} '{package}' {suffix}"

    return f'{verb} {suffix}'


def get_cli_command(
    action: SetupAction,
    environments: dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    scm_environments: dict[str, ScmEnvironment] | None = None,
) -> list[str]:
    """Gets the CLI command string for an action.

    Args:
        action: The action to get the command for.
        environments: Dict of instantiated environment plugins.
        strategy: The sync strategy (determines install vs upgrade command).
        project_environments: Dict of project-environment plugins.
        scm_environments: Dict of SCM-environment plugins.

    Returns:
        The CLI command as a list of strings, or empty list if not applicable.
    """
    cmd: list[str] = []
    match action.kind:
        case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
            if action.installer and action.package and action.installer in environments:
                env = environments[action.installer]
                if action.plugin_target is not None:
                    manager = find_plugin_manager(action.plugin_target.name, project_environments)
                    if manager is not None:
                        cmd = manager.plugin_add_command(action.package)
                elif strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
                    cmd = env.upgrade_command(action.package)
                else:
                    cmd = env.install_command(action.package)
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
            cmd = action.command or []
    return cmd


def build_actions(
    manifest: SetupManifest,
    environments: dict[str, Environment],
    strategy: SyncStrategy = SyncStrategy.MINIMAL,
    project_environments: dict[str, ProjectEnvironment] | None = None,
    scm_environments: dict[str, ScmEnvironment] | None = None,
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
        environments: Dict of instantiated environment plugins.
        strategy: The sync strategy (used for description text).
        project_environments: Dict of project-environment plugins.
        scm_environments: Dict of SCM-environment plugins.

    Returns:
        List of actions to perform.
    """
    actions: list[SetupAction] = []
    proj_envs = project_environments or {}
    scm_envs = scm_environments or {}

    all_plugins = {**environments, **proj_envs, **scm_envs}
    resolver = BackendResolver(all_plugins, manifest.preferences)

    verb = STRATEGY_VERB[strategy]

    # Iterate each kind section
    for kind, ecosystem, packages in manifest.iter_sections():
        installer = resolver.resolve(kind, ecosystem)

        # Actions of any kind are deferred when no backend is
        # available at preview time — the prerequisite may be installed
        # in an earlier phase (e.g. pip becomes available after pim
        # installs a Python runtime, pipx becomes available after pip
        # installs it, pyenv is installed via brew, etc.).  The
        # execution engine re-discovers plugins and resolves deferred
        # actions at each phase boundary.
        if installer is None:
            logger.info(
                "No installer available yet for (%s, '%s'); deferring its entries",
                kind.value,
                ecosystem,
            )

        # Project kind produces a single sync action
        if kind == PluginKind.PROJECT:
            actions.append(
                SetupAction(
                    description=action_description(kind, verb, installer),
                    kind=kind,
                    ecosystem=ecosystem,
                    installer=installer,
                )
            )
            continue

        # SCM kind produces one clone action per repository URL
        if kind == PluginKind.SCM:
            for package in packages:
                if not package.is_applicable():
                    continue
                actions.append(
                    SetupAction(
                        description=action_description(kind, verb, installer, package=package.name),
                        kind=kind,
                        ecosystem=ecosystem,
                        installer=installer,
                        package=package.name,
                        package_description=package.description,
                    )
                )
            continue

        for package in packages:
            if not package.is_applicable():
                continue
            actions.append(
                SetupAction(
                    description=action_description(kind, verb, installer, package=package.name),
                    kind=kind,
                    ecosystem=ecosystem,
                    installer=installer,
                    package=package.name,
                    package_description=package.description,
                    include_prereleases=package.include_prereleases,
                )
            )

            # Emit plugin-management actions for declared plugins
            for plugin_spec in package.plugins:
                actions.append(
                    SetupAction(
                        description=action_description(
                            kind,
                            verb,
                            installer,
                            package=plugin_spec.name,
                            plugin_target=package.name,
                        ),
                        kind=kind,
                        ecosystem=ecosystem,
                        installer=installer,
                        package=plugin_spec.name,
                        plugin_target=package.name,
                        include_prereleases=plugin_spec.include_prereleases,
                    )
                )

    # Add post-sync command actions (kind=None)
    for command_str in manifest.post_sync:
        command_parts = shlex.split(command_str)
        actions.append(
            SetupAction(
                description=f'Run: {command_str}',
                command=command_parts,
            )
        )

    return actions


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
    logger.info(f'Parsing manifest from: {path}')

    result = find_manifest(path)
    plugins = discover_all_plugins()
    actions = build_actions(
        result.manifest,
        plugins.environments,
        strategy,
        plugins.project_environments,
        plugins.scm_environments,
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
    )
