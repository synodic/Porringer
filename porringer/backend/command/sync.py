"""The sync command module."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shlex
import subprocess
import tomllib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.backend.backend import BackendResolver
from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.plugin_schema.project_environment import ProjectEnvironment, ProjectSyncParameters
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Package, PackageRef, Plugin, PluginKind
from porringer.schema import (
    BatchSetupResults,
    DownloadParameters,
    DownloadResult,
    ManifestDiagnostic,
    ManifestDiagnosticSeverity,
    ManifestMetadata,
    ManifestValidationCode,
    ManifestValidationResult,
    ProgressCallback,
    ProgressEvent,
    ProgressEventKind,
    SetupAction,
    SetupActionResult,
    SetupManifest,
    SetupParameters,
    SetupResults,
    SkipReason,
    SubActionProgress,
    SyncStrategy,
)
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError, PluginError
from porringer.utility.utility import canonicalize_type

logger = logging.getLogger(__name__)


# Execution order for phased setup.  ``None`` represents post-sync commands.
_PHASE_ORDER: list[PluginKind | None] = [
    PluginKind.RUNTIME,
    PluginKind.PACKAGE,
    PluginKind.TOOL,
    PluginKind.PROJECT,
    PluginKind.SCM,
    None,
]


# Maps SyncStrategy to the human-readable verb used in action descriptions.
_STRATEGY_VERB: dict[SyncStrategy, str] = {
    SyncStrategy.MINIMAL: 'Install',
    SyncStrategy.LATEST: 'Upgrade',
    SyncStrategy.EXACT: 'Ensure',
}


@dataclass(frozen=True)
class _ExecutionPhaseContext:
    """Shared context for execution phase operations."""

    environments: dict[str, Environment]
    project_environments: dict[str, ProjectEnvironment] | None
    parameters: SetupParameters
    skip_project: bool
    working_dir: Path
    event_queue: asyncio.Queue[ProgressEvent | None] | None


class SyncCommands:
    """Update commands for downloading updates and setting up from manifests."""

    def __init__(self, cache_manager: DirectoryCacheManager | None = None) -> None:
        """Initialize the SyncCommands class.

        Args:
            cache_manager: Optional cache manager for resolving cached paths.
        """
        self._cache_manager = cache_manager

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

    # --- Validation Methods ---

    @staticmethod
    def validate_manifest(path: Path) -> ManifestValidationResult:
        """Validate a manifest for errors without executing any operations.

        Checks syntax, schema version, required fields, plugin availability,
        package-name validity, and duplicate packages across plugins.

        Args:
            path: Path to a manifest file or directory containing one.

        Returns:
            Structured validation result with diagnostics.
        """
        diagnostics: list[ManifestDiagnostic] = []

        def _error(field: str, message: str, code: ManifestValidationCode) -> None:
            diagnostics.append(ManifestDiagnostic(field, message, code, ManifestDiagnosticSeverity.ERROR))

        def _warning(field: str, message: str, code: ManifestValidationCode) -> None:
            diagnostics.append(ManifestDiagnostic(field, message, code, ManifestDiagnosticSeverity.WARNING))

        manifest = SyncCommands._load_manifest_for_validation(path, _error)
        if manifest is None:
            return ManifestValidationResult(diagnostics=diagnostics)

        SyncCommands._validate_schema_version(manifest, _error)

        environments = SyncCommands._discover_plugins('environment', Environment, check_dependencies=True)
        project_environments = SyncCommands._discover_plugins('project_environment', ProjectEnvironment)
        scm_environments = SyncCommands._discover_plugins('scm', ScmEnvironment)
        all_plugins = {**environments, **project_environments, **scm_environments}
        resolver = BackendResolver(all_plugins, manifest.preferences)

        SyncCommands._validate_backends(manifest, resolver, _error)
        SyncCommands._validate_package_names(manifest, resolver, _warning)
        SyncCommands._validate_duplicate_packages(manifest, _warning)

        return ManifestValidationResult(diagnostics=diagnostics)

    @staticmethod
    def _load_manifest_for_validation(
        path: Path,
        error_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> SetupManifest | None:
        """Load a manifest for validation or record diagnostics."""
        if not path.exists():
            error_callback('path', f'Path does not exist: {path}', ManifestValidationCode.PATH_NOT_FOUND)
            return None

        try:
            _, manifest = SyncCommands._find_manifest(path)
        except ManifestError as exc:
            msg = str(exc)
            code = SyncCommands._map_manifest_error_code(msg)
            error_callback('', msg, code)
            return None

        return manifest

    @staticmethod
    def _map_manifest_error_code(message: str) -> ManifestValidationCode:
        """Map manifest loading errors to validation codes."""
        if 'No manifest found' in message or 'No [tool.porringer]' in message:
            return ManifestValidationCode.NO_MANIFEST
        if 'Invalid JSON' in message or 'Invalid TOML' in message:
            return ManifestValidationCode.SYNTAX_ERROR
        return ManifestValidationCode.SCHEMA_INVALID

    @staticmethod
    def _validate_schema_version(
        manifest: SetupManifest,
        error_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate manifest schema version."""
        supported_versions = {'1'}
        if manifest.version not in supported_versions:
            error_callback(
                'version',
                f"Unsupported schema version '{manifest.version}'. Supported: {', '.join(sorted(supported_versions))}",
                ManifestValidationCode.UNSUPPORTED_VERSION,
            )

    @staticmethod
    def _validate_backends(
        manifest: SetupManifest,
        resolver: BackendResolver,
        error_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate that each (kind, ecosystem) in the manifest resolves to a plugin."""
        for kind, ecosystem, _packages in manifest.iter_sections():
            installer = resolver.resolve(kind, ecosystem)
            if installer is None:
                error_callback(
                    f'{kind.value}.{ecosystem}',
                    f"No available installer for ({kind.value}, '{ecosystem}')",
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )

    @staticmethod
    def _validate_package_names(
        manifest: SetupManifest,
        resolver: BackendResolver,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate package specifiers in a manifest.

        PEP 440 validation is applied when the resolved plugin declares
        ``package_name_validator() == 'pep440'``.  Other ecosystems
        accept any non-empty package name.
        """
        for kind, ecosystem, packages in manifest.iter_sections():
            validator = resolver.validator_for(kind, ecosystem)

            for j, spec in enumerate(packages):
                name_str = str(spec.name)
                if not name_str.strip():
                    warning_callback(
                        f'{kind.value}.{ecosystem}[{j}].name',
                        'Empty package name',
                        ManifestValidationCode.INVALID_PACKAGE_NAME,
                    )
                    continue
                if validator == 'pep440':
                    try:
                        Requirement(name_str)
                    except InvalidRequirement as exc:
                        warning_callback(
                            f'{kind.value}.{ecosystem}[{j}].name',
                            f"Invalid package specifier '{spec.name}': {exc}",
                            ManifestValidationCode.INVALID_PACKAGE_NAME,
                        )

    @staticmethod
    def _validate_duplicate_packages(
        manifest: SetupManifest,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Warn when packages appear under multiple sections."""
        seen: dict[str, list[str]] = {}
        for kind, ecosystem, packages in manifest.iter_sections():
            label = f'{kind.value}.{ecosystem}'
            for spec in packages:
                canonical = str(canonicalize_name(spec.name.name))
                seen.setdefault(canonical, []).append(label)

        for pkg_name, locations in seen.items():
            if len(locations) > 1:
                warning_callback(
                    kind.value,
                    f"Package '{pkg_name}' is listed under multiple sections: {', '.join(locations)}",
                    ManifestValidationCode.DUPLICATE_PACKAGE,
                )

    @staticmethod
    def manifest_schema() -> dict:
        """Export a JSON Schema representation of the manifest format.

        Returns:
            A dict containing the JSON Schema for ``SetupManifest``.
        """
        return SetupManifest.model_json_schema()

    # --- Manifest/Setup Methods ---

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

    @staticmethod
    def _find_manifest(path: Path) -> tuple[Path, SetupManifest]:
        """Finds and loads the setup manifest from the given path.

        Args:
            path: Path to a manifest file or directory containing one.

        Returns:
            Tuple of (manifest_path, parsed_manifest).

        Raises:
            ManifestError: If no valid manifest is found.
        """
        if path.is_file():
            return SyncCommands._load_manifest_file(path)

        if path.is_dir():
            # Try porringer.json first, then pyproject.toml
            porringer_file = path / 'porringer.json'
            if porringer_file.exists():
                return SyncCommands._load_manifest_file(porringer_file)

            pyproject_file = path / 'pyproject.toml'
            if pyproject_file.exists():
                return SyncCommands._load_pyproject_manifest(pyproject_file)

            raise ManifestError(
                f"No manifest found in directory: {path}. Expected 'porringer.json' or 'pyproject.toml'"
            )

        raise ManifestError(f'Path does not exist: {path}')

    @staticmethod
    def _load_manifest_file(path: Path) -> tuple[Path, SetupManifest]:
        """Loads a manifest from a porringer.json JSON file or pyproject.toml.

        Args:
            path: Path to the manifest file.

        Returns:
            Tuple of (path, parsed_manifest).

        Raises:
            ManifestError: If the file cannot be parsed.
        """
        if path.suffix == '.toml' or path.name == 'pyproject.toml':
            return SyncCommands._load_pyproject_manifest(path)

        # Assume JSON for porringer.json or other files
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return path, SetupManifest.model_validate(data)
        except json.JSONDecodeError as e:
            raise ManifestError(f'Invalid JSON in manifest {path}: {e}') from e
        except Exception as e:
            raise ManifestError(f'Failed to load manifest {path}: {e}') from e

    @staticmethod
    def _load_pyproject_manifest(path: Path) -> tuple[Path, SetupManifest]:
        """Loads a manifest from pyproject.toml [tool.porringer] section.

        Args:
            path: Path to pyproject.toml.

        Returns:
            Tuple of (path, parsed_manifest).

        Raises:
            ManifestError: If the file cannot be parsed or section is missing.
        """
        try:
            with open(path, 'rb') as f:
                data = tomllib.load(f)

            tool_section = data.get('tool', {})
            porringer_section = tool_section.get('porringer')

            if porringer_section is None:
                raise ManifestError(f'No [tool.porringer] section found in {path}')

            return path, SetupManifest.model_validate(porringer_section)
        except tomllib.TOMLDecodeError as e:
            raise ManifestError(f'Invalid TOML in {path}: {e}') from e
        except ManifestError:
            raise
        except Exception as e:
            raise ManifestError(f'Failed to load pyproject.toml manifest {path}: {e}') from e

    @staticmethod
    def _discover_plugins[T: Plugin](group: str, base_class: type[T], **kwargs: bool) -> dict[str, T]:
        """Discover and instantiate plugins, returning a name-keyed dict.

        Args:
            group: Entry-point group suffix (e.g. ``'environment'``).
            base_class: Expected base class for the plugins.
            **kwargs: Forwarded to :meth:`Builder.find_plugins`
                (e.g. ``check_dependencies=True``).

        Returns:
            Dict mapping canonical plugin name to instantiated plugin.
        """
        infos = Builder.find_plugins(group, base_class, **kwargs)
        instances = Builder.build_plugins(infos)
        return {canonicalize_type(type(inst)).name: inst for inst in instances}

    @staticmethod
    def _get_cli_command(
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
                    if strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
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

    @staticmethod
    def _build_actions(
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
        repository URL.  The ``strategy`` parameter controls only the
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

        verb = _STRATEGY_VERB[strategy]

        # Iterate each kind section
        for kind, ecosystem, packages in manifest.iter_sections():
            installer = resolver.resolve(kind, ecosystem)

            # TOOL-kind actions are deferred when no backend is available
            # at preview time — the prerequisite tool may be installed in
            # an earlier phase (e.g. pipx installed via pip).
            if installer is None and kind != PluginKind.TOOL:
                logger.warning(
                    "No installer available for (%s, '%s'); skipping its entries",
                    kind.value,
                    ecosystem,
                )
                continue

            # Project kind produces a single sync action
            if kind == PluginKind.PROJECT:
                actions.append(
                    SetupAction(
                        description=f'Sync project via {installer}',
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
                            description=f"Clone '{package.name}' via {installer}",
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
                desc = (
                    f"{verb} '{package.name}' via {installer}" if installer else f"{verb} '{package.name}' (deferred)"
                )
                actions.append(
                    SetupAction(
                        description=desc,
                        kind=kind,
                        ecosystem=ecosystem,
                        installer=installer,
                        package=package.name,
                        package_description=package.description,
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

    @staticmethod
    def preview_single(path: Path, strategy: SyncStrategy = SyncStrategy.MINIMAL) -> SetupResults:
        """Previews the setup actions for a single path without executing them.

        Args:
            path: Path to manifest file or directory containing one.
            strategy: The sync strategy.

        Returns:
            SetupResults containing the list of actions that would be performed.

        Raises:
            ManifestError: If the manifest cannot be found or parsed.
        """
        logger.info(f'Previewing setup from: {path}')

        manifest_path, manifest = SyncCommands._find_manifest(path)
        environments = SyncCommands._discover_plugins('environment', Environment, check_dependencies=True)
        project_environments = SyncCommands._discover_plugins('project_environment', ProjectEnvironment)
        scm_environments = SyncCommands._discover_plugins('scm', ScmEnvironment)
        actions = SyncCommands._build_actions(manifest, environments, strategy, project_environments, scm_environments)
        metadata = ManifestMetadata(
            name=manifest.name,
            description=manifest.description,
            author=manifest.author,
            url=str(manifest.url) if manifest.url else None,
        )

        return SetupResults(actions=actions, manifest_path=manifest_path, metadata=metadata)

    def preview_batch(self, parameters: SetupParameters) -> BatchSetupResults:
        """Preview setup actions for multiple paths.

        Args:
            parameters: The setup parameters with paths or group.

        Returns:
            BatchSetupResults containing previews for each manifest.
        """
        paths = self._resolve_paths(parameters)
        logger.info(f'Previewing setup for {len(paths)} path(s)')

        manifest_results: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = []

        for path in paths:
            try:
                result = self.preview_single(path, strategy=parameters.strategy)
                manifest_results.append(result)
            except ManifestError as e:
                failed_paths.append((path, str(e.error)))
                if parameters.fail_fast:
                    break

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)

    @staticmethod
    def _dry_run_action(
        action: SetupAction,
        environments: dict[str, Environment],
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
        *,
        prior_results: list[SetupActionResult] | None = None,
    ) -> SetupActionResult:
        """Simulates executing an action in dry-run mode.

        For package/tool/runtime actions, real system state is checked so
        that the result accurately reflects whether the action would be
        skipped.

        For post-sync commands (``kind is None``), the command is skipped
        when all prior results were themselves skipped (nothing changed).

        Args:
            action: The action to simulate.
            environments: Dict of instantiated environment plugins.
            strategy: The sync strategy (affects skip logic for packages).
            prior_results: Results from earlier phases (used by commands).

        Returns:
            The simulated result.
        """
        match action.kind:
            case PluginKind.PACKAGE | PluginKind.TOOL | PluginKind.RUNTIME:
                return SyncCommands._dry_run_package_action(action, environments, strategy)
            case PluginKind.PROJECT | PluginKind.SCM:
                return SetupActionResult(action=action, success=True)
            case None:
                # Post-sync command
                if prior_results and all(r.skipped for r in prior_results):
                    return SetupActionResult(
                        action=action,
                        success=True,
                        skipped=True,
                        skip_reason=SkipReason.NOTHING_CHANGED,
                        message='All prerequisites already satisfied',
                    )
                return SetupActionResult(action=action, success=True)
            case _:
                return SetupActionResult(action=action, success=False, message=f'Unknown action kind: {action.kind}')

    @staticmethod
    def _dry_run_package_action(
        action: SetupAction,
        environments: dict[str, Environment],
        strategy: SyncStrategy,
    ) -> SetupActionResult:
        """Simulate a package action in dry-run mode."""
        if action.installer is None or action.package is None or action.installer not in environments:
            return SetupActionResult(action=action, success=True)

        # Determine name validator from the plugin
        env = environments[action.installer]
        validator = type(env).package_name_validator()

        try:
            installed_packages = environments[action.installer].packages()
            is_installed, installed_detail = SyncCommands._is_package_installed(
                action.package, installed_packages, validator, action.kind
            )
        except PluginError as e:
            logger.debug(f'Dry-run: plugin error checking packages for {action.installer}: {e}')
            return SetupActionResult(action=action, success=True)
        except Exception as e:
            logger.debug(f'Dry-run: could not check installed packages for {action.installer}: {e}')
            return SetupActionResult(action=action, success=True)

        if strategy == SyncStrategy.MINIMAL:
            if is_installed:
                logger.info(f"Dry-run: skipping '{action.package}': {installed_detail}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=SkipReason.ALREADY_INSTALLED,
                    message=installed_detail,
                )
        elif not is_installed:
            return SetupActionResult(
                action=action,
                success=True,
                message='not installed, will install instead',
            )

        return SetupActionResult(action=action, success=True)

    @staticmethod
    def _is_package_installed(
        package: PackageRef,
        installed_packages: list[Package],
        name_validator: str | None = None,
        kind: PluginKind | None = None,
    ) -> tuple[bool, str | None]:
        """Checks if a package is already installed with a compatible version.

        When *name_validator* is ``'pep440'``, uses PEP 440 canonicalization
        and specifier matching.  Otherwise, uses case-insensitive name
        comparison and simple string version equality.

        For ``RUNTIME`` actions, name comparison uses prefix matching so
        that a request for ``3.14`` matches an installed ``3.14-64``
        (architecture-qualified tag).

        Args:
            package: The package reference
            installed_packages: List of installed packages from the environment
            name_validator: The validator tag declared by the resolved plugin
            kind: The plugin kind (enables prefix matching for RUNTIME)

        Returns:
            Tuple of (is_installed, skip_reason or None)
        """
        is_pep440 = name_validator == 'pep440'
        is_runtime = kind == PluginKind.RUNTIME

        for installed in installed_packages:
            if is_runtime:
                # Runtime tags use prefix matching: "3.14" matches "3.14-64"
                if not installed.name.lower().startswith(package.name.lower()):
                    continue
            elif is_pep440:
                if canonicalize_name(installed.name) != canonicalize_name(package.name):
                    continue
            elif installed.name.lower() != package.name.lower():
                continue

            # Name matched
            if not package.constraint:
                return True, f'{installed.name}=={installed.version} already installed'

            if installed.version is not None:
                if is_pep440:
                    try:
                        req = Requirement(str(package))
                        if Version(installed.version) in req.specifier:
                            return True, f'{installed.name}=={installed.version} satisfies {package}'
                    except InvalidVersion, InvalidRequirement:
                        pass
                else:
                    # Non-PEP-440: installed means installed; constraint
                    # satisfaction is left to the underlying tool.
                    return True, f'{installed.name}=={installed.version} already installed'

        return False, None

    @staticmethod
    def _execute_run_command(action: SetupAction, working_dir: Path, timeout: int) -> SetupActionResult:
        """Executes a post-install command.

        Args:
            action: The command action.
            working_dir: Working directory for the command.
            timeout: Timeout in seconds.

        Returns:
            The result of the command execution.
        """
        if action.command is None or len(action.command) == 0:
            return SetupActionResult(action=action, success=False, message='No command specified')

        logger.info(f'Running command: {" ".join(action.command)}')

        try:
            result = subprocess.run(
                action.command,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

            if result.returncode == 0:
                return SetupActionResult(action=action, success=True)
            else:
                stderr = result.stderr.strip() if result.stderr else 'Unknown error'
                return SetupActionResult(
                    action=action, success=False, message=f'Exit code {result.returncode}: {stderr}'
                )
        except subprocess.TimeoutExpired:
            message = f'Command timed out after {timeout} seconds'
            logger.error(message)
            return SetupActionResult(action=action, success=False, message=message)
        except FileNotFoundError:
            message = f'Command not found: {action.command[0]}'
            return SetupActionResult(action=action, success=False, message=message)
        except Exception as e:
            return SetupActionResult(action=action, success=False, message=str(e))

    async def _execute_package(
        self,
        action: SetupAction,
        environments: dict[str, Environment],
        strategy: SyncStrategy,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupActionResult:
        """Execute a package install or upgrade based on the strategy.

        In MINIMAL strategy, skips already-installed packages.
        In LATEST/EXACT strategy, upgrades installed packages and falls back to
        install for packages that are not yet present.

        Args:
            action: The package action.
            environments: Dict of instantiated environment plugins.
            strategy: The sync strategy.
            event_queue: Optional queue to emit sub-action events into.

        Returns:
            The result of the operation.
        """
        if action.installer is None or action.package is None:
            return SetupActionResult(action=action, success=False, message='Installer or package not specified')

        if action.installer not in environments:
            msg = f"Installer '{action.installer}' is not available"
            return SetupActionResult(action=action, success=False, message=msg)

        environment = environments[action.installer]

        # Check if package is already installed
        is_installed = False
        installed_detail: str | None = None
        validator = type(environment).package_name_validator()
        try:
            loop = asyncio.get_running_loop()
            installed_packages = await loop.run_in_executor(None, environment.packages)
            is_installed, installed_detail = SyncCommands._is_package_installed(
                action.package, installed_packages, validator, action.kind
            )
        except PluginError as e:
            logger.debug(f'Plugin error checking packages for {action.installer}: {e}')
        except Exception as e:
            logger.debug(f'Could not check installed packages for {action.installer}: {e}')

        if strategy == SyncStrategy.MINIMAL:
            if is_installed:
                logger.info(f"Skipping '{action.package}': {installed_detail}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=SkipReason.ALREADY_INSTALLED,
                    message=installed_detail,
                )
            logger.info(f"Installing '{action.package}' via {action.installer}")
            return await self._attempt_package_operation(action, environment, SyncStrategy.MINIMAL, event_queue)
        else:
            # UPGRADE or ENSURE
            if not is_installed:
                logger.info(f"'{action.package}' not installed via {action.installer}, falling back to install")
                return await self._attempt_package_operation(action, environment, SyncStrategy.MINIMAL, event_queue)
            logger.info(f"Upgrading '{action.package}' via {action.installer}")
            return await self._attempt_package_operation(action, environment, strategy, event_queue)

    @staticmethod
    async def _attempt_package_operation(
        action: SetupAction,
        environment: Environment,
        strategy: SyncStrategy,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupActionResult:
        """Attempt to install or upgrade a package via the given environment plugin.

        Args:
            action: The package action.
            environment: The environment plugin to use.
            strategy: Whether to install or upgrade.
            event_queue: Optional queue to emit sub-action events into.

        Returns:
            The result of the attempt.
        """
        success = False
        message = ''

        # Build a progress_callback that emits SubActionProgress into the event queue
        sub_action_cb = None
        if event_queue is not None:
            eq = event_queue

            def sub_action_cb(update: SubActionProgress) -> None:
                eq.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.SUB_ACTION_PROGRESS, action=action, sub_action=update)
                )

        is_install = strategy == SyncStrategy.MINIMAL
        verb_past = 'Installed' if is_install else 'Upgraded'
        verb_inf = 'install' if is_install else 'upgrade'

        try:
            if action.package is None:
                return SetupActionResult(action=action, success=False, message='No package specified')
            params = PackageParameters(
                package=action.package,
                dry=False,
                progress_callback=sub_action_cb,
            )
            if is_install:
                result = await environment.async_install(params)
            else:
                result = await environment.async_upgrade(params)

            if result is not None:
                success = True
                message = f'{verb_past} {result.name}'
            else:
                message = f"Failed to {verb_inf} '{action.package}'"
        except PluginError as e:
            logger.error(f'Plugin error {verb_inf}ing {action.package}: {e}')
            message = str(e)
        except asyncio.CancelledError:
            logger.error(f'{verb_past.rstrip("d")} cancelled for {action.package}')
            message = f'{verb_past.rstrip("d")} cancelled'
        except TimeoutError as e:
            logger.error(f'Timeout {verb_inf}ing {action.package}: {e}')
            message = str(e)
        except Exception as e:
            message = str(e)

        return SetupActionResult(action=action, success=success, message=message)

    async def _execute_package_actions(
        self,
        package_actions: list[SetupAction],
        environments: dict[str, Environment],
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Execute PACKAGE actions with parallel support.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        if parameters.dry_run:
            return (
                self._dry_run_package_actions(package_actions, environments, parameters.strategy, event_queue),
                True,
            )

        parallel_actions, sequential_actions = self._group_actions_by_parallelism(package_actions, environments)

        results: list[SetupActionResult] = []

        # Execute parallel actions concurrently
        if parallel_actions:
            parallel_results, should_continue = await self._run_parallel_packages(
                parallel_actions, environments, parameters, event_queue
            )
            results.extend(parallel_results)
            if not should_continue:
                return results, False

        # Execute sequential actions one at a time
        sequential_results, should_continue = await self._run_sequential_packages(
            sequential_actions, environments, parameters, event_queue
        )
        results.extend(sequential_results)

        return results, should_continue

    def _dry_run_package_actions(
        self,
        package_actions: list[SetupAction],
        environments: dict[str, Environment],
        strategy: SyncStrategy,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Execute dry-run for package actions."""
        results: list[SetupActionResult] = []
        for action in package_actions:
            result = self._dry_run_action(action, environments, strategy)
            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
        return results

    @staticmethod
    def _group_actions_by_parallelism(
        install_actions: list[SetupAction],
        environments: dict[str, Environment],
    ) -> tuple[list[SetupAction], list[SetupAction]]:
        """Group actions into parallel and sequential based on plugin support."""
        parallel_actions: list[SetupAction] = []
        sequential_actions: list[SetupAction] = []

        for action in install_actions:
            supports = (
                action.installer
                and action.installer in environments
                and environments[action.installer].supports_parallel()
            )
            if supports:
                parallel_actions.append(action)
            else:
                sequential_actions.append(action)

        return parallel_actions, sequential_actions

    async def _run_sequential_packages(
        self,
        sequential_actions: list[SetupAction],
        environments: dict[str, Environment],
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Run package actions sequentially."""
        results: list[SetupActionResult] = []
        for action in sequential_actions:
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            result = await self._execute_package(action, environments, parameters.strategy, event_queue)
            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            if not result.success and not result.skipped and parameters.fail_fast:
                logger.error(f'Action failed: {action.description} - {result.message}')
                return results, False
        return results, True

    async def _run_parallel_packages(
        self,
        parallel_actions: list[SetupAction],
        environments: dict[str, Environment],
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Run package actions in parallel using TaskGroup.

        Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.
        All tasks are automatically cancelled if any raises an unhandled exception.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        results: dict[int, SetupActionResult] = {}
        action_indices = {id(action): i for i, action in enumerate(parallel_actions)}

        async def package_with_event(action: SetupAction) -> None:
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
            try:
                result = await self._execute_package(action, environments, parameters.strategy, event_queue)
            except Exception as e:
                result = SetupActionResult(action=action, success=False, message=str(e))
            if event_queue is not None:
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            results[action_indices[id(action)]] = result

        try:
            async with asyncio.TaskGroup() as tg:
                for action in parallel_actions:
                    tg.create_task(package_with_event(action))
        except ExceptionGroup as eg:
            # TaskGroup raises ExceptionGroup if any task fails with unhandled exception
            # Our package_with_event catches exceptions, so this shouldn't happen normally
            logger.error(f'Parallel package operation failed with exceptions: {eg.exceptions}')

        # Convert dict to ordered list
        result_list = [results.get(i) for i in range(len(parallel_actions))]
        final_results: list[SetupActionResult] = []

        for action, maybe_result in zip(parallel_actions, result_list, strict=False):
            action_result: SetupActionResult
            if maybe_result is None:
                # Task was cancelled before completing
                action_result = SetupActionResult(action=action, success=False, message='Task cancelled')
            else:
                action_result = maybe_result
            final_results.append(action_result)
            if not action_result.success and not action_result.skipped and parameters.fail_fast:
                logger.error(f'Action failed: {action.description} - {action_result.message}')
                return final_results, False

        return final_results, True

    async def _execute_command_actions(
        self,
        command_actions: list[SetupAction],
        context: _ExecutionPhaseContext,
        *,
        prior_results: list[SetupActionResult] | None = None,
    ) -> list[SetupActionResult]:
        """Execute RUN_COMMAND actions sequentially."""
        results: list[SetupActionResult] = []
        for action in command_actions:
            if context.parameters.dry_run:
                result = self._dry_run_action(
                    action,
                    context.environments,
                    context.parameters.strategy,
                    prior_results=prior_results,
                )
            else:
                result = self._execute_run_command(action, context.working_dir, context.parameters.timeout)
            results.append(result)
            if context.event_queue is not None:
                context.event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
                context.event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            if not result.success and not result.skipped:
                logger.error(f'Action failed: {action.description} - {result.message}')
                if context.parameters.fail_fast:
                    break
        return results

    @staticmethod
    def _determine_working_dir(parameters: SetupParameters, path: Path) -> Path:
        """Determine the working directory for command execution.

        Args:
            parameters: Setup parameters that may specify a project directory.
            path: The path being processed.

        Returns:
            The working directory to use.
        """
        if isinstance(parameters.project_directory, Path):
            return parameters.project_directory
        return path if path.is_dir() else path.parent

    async def _handle_project_phase(
        self,
        project_actions: list[SetupAction],
        context: _ExecutionPhaseContext,
    ) -> list[SetupActionResult]:
        """Execute or skip project sync actions depending on context.

        Args:
            project_actions: The project-kind actions to process.
            context: Execution phase context with parameters and flags.

        Returns:
            Results for each project action.
        """
        if not context.skip_project:
            return await self._execute_project_sync_actions(
                project_actions,
                context.project_environments,
                context.working_dir,
                context.parameters,
                context.event_queue,
            )
        return self._skip_actions(
            project_actions,
            SkipReason.NO_PROJECT_DIRECTORY,
            'No project directory provided',
            context.event_queue,
        )

    async def _execute_single(
        self,
        actions: list[SetupAction],
        path: Path,
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupResults:
        """Execute setup actions for a single path with parallel support.

        Execution is **phased** so that each layer's prerequisite tools
        are available before they are needed:

        1. **Runtime** — install/resolve language runtimes (pim, pyenv).
        2. **Package** — install packages into the current environment
           (pip, uv).  This may install tool prerequisites such as pipx.
        3. **Tool** — install isolated CLI tools (pipx).  Plugins are
           re-discovered after Phase 2 so that newly-installed backends
           are available.  Deferred actions whose ``installer`` was
           ``None`` at preview time are resolved here.
        4. **Project sync** — run ``pdm install`` / ``uv sync`` in the
           manifest directory.
        5. **SCM clone** — clone source-control repositories.
        6. **Post-sync commands** — run arbitrary shell commands.

        Args:
            actions: The list of actions to execute (from preview).
            path: The path this execution is for (used for working directory).
            parameters: The setup parameters.
            event_queue: Optional queue to emit ``ProgressEvent`` items into.

        Returns:
            SetupResults containing the results of each action.
        """
        logger.info(f'Executing {len(actions)} setup actions async (dry_run={parameters.dry_run})')

        results: list[SetupActionResult] = []
        environments = self._discover_plugins('environment', Environment, check_dependencies=True)
        project_environments = self._discover_plugins('project_environment', ProjectEnvironment)
        scm_environments = self._discover_plugins('scm', ScmEnvironment)

        skip_project = parameters.project_directory is False
        working_dir = self._determine_working_dir(parameters, path)

        # Populate CLI commands for all resolved actions
        for action in actions:
            action.cli_command = SyncCommands._get_cli_command(
                action, environments, parameters.strategy, project_environments, scm_environments
            )

        phases = self._group_actions_by_phase(actions)

        # --- Phase 1: runtime-provider actions (pim / pyenv) ---------------
        if phases[PluginKind.RUNTIME]:
            runtime_results, should_continue = await self._execute_package_actions(
                phases[PluginKind.RUNTIME],
                environments,
                parameters,
                event_queue,
            )
            results.extend(runtime_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)
            self._propagate_runtime(phases[PluginKind.RUNTIME], environments, project_environments)

        # --- Phase 2a: package-kind actions (pip, uv, etc.) ---------------
        if phases[PluginKind.PACKAGE]:
            package_results, should_continue = await self._execute_package_actions(
                phases[PluginKind.PACKAGE],
                environments,
                parameters,
                event_queue,
            )
            results.extend(package_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)

        # --- Phase 2b: tool-kind actions (pipx, etc.) ---------------------
        # Re-discover plugins so that tools installed in Phase 2a
        # (e.g. pipx via pip) are now available as backends.
        if phases[PluginKind.TOOL]:
            environments = self._discover_plugins('environment', Environment, check_dependencies=True)

            # Resolve deferred tool actions whose installer was None
            self._resolve_deferred_actions(phases[PluginKind.TOOL], environments, parameters.strategy)

            # Update CLI commands for newly-resolved tool actions
            for action in phases[PluginKind.TOOL]:
                action.cli_command = SyncCommands._get_cli_command(
                    action, environments, parameters.strategy, project_environments, scm_environments
                )

            tool_results, should_continue = await self._execute_package_actions(
                phases[PluginKind.TOOL],
                environments,
                parameters,
                event_queue,
            )
            results.extend(tool_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)

        # --- Phase 3: project sync ----------------------------------------
        if phases[PluginKind.PROJECT]:
            # Re-discover project environments in case tools installed in
            # earlier phases provide new project-environment backends.
            project_environments = self._discover_plugins('project_environment', ProjectEnvironment)

            results.extend(
                await self._handle_project_phase(
                    phases[PluginKind.PROJECT],
                    _ExecutionPhaseContext(
                        environments=environments,
                        project_environments=project_environments,
                        parameters=parameters,
                        skip_project=skip_project,
                        working_dir=working_dir,
                        event_queue=event_queue,
                    ),
                )
            )

        # --- Phase 4: SCM clone -------------------------------------------
        if phases[PluginKind.SCM]:
            results.extend(
                await self._execute_scm_actions(
                    phases[PluginKind.SCM],
                    scm_environments,
                    working_dir,
                    parameters,
                    event_queue,
                )
            )

        # --- Phase 5: post-sync commands ----------------------------------
        if phases[None]:
            context = _ExecutionPhaseContext(
                environments=environments,
                project_environments=project_environments,
                parameters=parameters,
                skip_project=skip_project,
                working_dir=working_dir,
                event_queue=event_queue,
            )
            results.extend(await self._execute_command_actions(phases[None], context, prior_results=results))

        return SetupResults(actions=actions, results=results)

    @staticmethod
    def _group_actions_by_phase(
        actions: list[SetupAction],
    ) -> dict[PluginKind | None, list[SetupAction]]:
        """Group actions into phase buckets keyed by :class:`PluginKind`.

        Post-sync commands (``kind is None``) are stored under the
        ``None`` key.

        Returns:
            Dict mapping each phase to its action list.
        """
        phases: dict[PluginKind | None, list[SetupAction]] = {k: [] for k in _PHASE_ORDER}
        for action in actions:
            phases[action.kind].append(action)
        return phases

    @staticmethod
    def _propagate_runtime(
        runtime_actions: list[SetupAction],
        environments: dict[str, Environment],
        project_environments: dict[str, ProjectEnvironment] | None = None,
    ) -> None:
        """Resolve the interpreter path and propagate to downstream consumers.

        After runtime-provider actions complete, finds the first
        :class:`RuntimeProvider` that can resolve an executable and sets
        ``runtime_executable`` on all :class:`RuntimeConsumer` plugins
        whose ``consumed_runtime_kind`` matches the provider's
        ``provided_runtime_kind``.
        """
        proj_envs = project_environments or {}

        # Find a RuntimeProvider among the runtime action installers
        for action in runtime_actions:
            if action.installer is None or action.package is None:
                continue
            env = environments.get(action.installer)
            if env is None or not isinstance(env, RuntimeProvider):
                continue

            kind = cast(type[RuntimeProvider], type(env)).provided_runtime_kind()
            tag = action.package.name
            executable = env.resolve_executable(tag)
            if executable is None:
                logger.debug('RuntimeProvider %s could not resolve executable for tag %s', action.installer, tag)
                continue

            logger.info('Runtime resolved: %s -> %s', tag, executable)

            # Propagate to all plugins (environment + project-environment) that consume this runtime kind
            all_plugins: dict[str, Environment | ProjectEnvironment] = {**environments, **proj_envs}
            for name, downstream in all_plugins.items():
                if isinstance(downstream, RuntimeConsumer):
                    downstream_type = cast(type[RuntimeConsumer], type(downstream))
                    if downstream_type.consumed_runtime_kind() == kind:
                        downstream.runtime_executable = executable
                        logger.debug('Set runtime_executable on %s to %s', name, executable)

            # Use only the first successfully resolved runtime
            break

    @staticmethod
    def _resolve_deferred_actions(
        actions: list[SetupAction],
        environments: dict[str, Environment],
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
    ) -> None:
        """Resolve deferred actions whose ``installer`` is ``None``.

        After a preceding phase installs new tools (e.g. pip installs pipx),
        plugins are re-discovered and a fresh ``BackendResolver`` determines
        the correct backend for each deferred action.  Actions that still
        cannot be resolved are left with ``installer = None`` so that the
        normal execution path reports them as unavailable.

        Args:
            actions: Mutable list of actions to resolve in-place.
            environments: Freshly-discovered environment plugins.
            strategy: Sync strategy (for description verb).
        """
        deferred = [a for a in actions if a.installer is None and a.ecosystem is not None]
        if not deferred:
            return

        resolver = BackendResolver(environments)
        verb = _STRATEGY_VERB[strategy]

        for action in deferred:
            assert action.kind is not None
            assert action.ecosystem is not None
            installer = resolver.resolve(action.kind, action.ecosystem)
            if installer is not None:
                action.installer = installer
                if action.package is not None:
                    action.description = f"{verb} '{action.package}' via {installer}"
                logger.info('Deferred action resolved: %s -> %s', action.description, installer)
            else:
                logger.warning('Deferred action still unresolved: %s', action.description)

    @staticmethod
    def _skip_actions(
        actions: list[SetupAction],
        skip_reason: SkipReason,
        message: str,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Skip a list of actions, emitting progress events and a warning for each.

        Args:
            actions: The actions to skip.
            skip_reason: Machine-readable skip code.
            message: Human-readable skip detail.
            event_queue: Optional queue for progress events.

        Returns:
            List of skipped action results.
        """
        results: list[SetupActionResult] = []
        for action in actions:
            logger.warning("Skipping '%s': %s", action.description, message)
            result = SetupActionResult(
                action=action, success=True, skipped=True, skip_reason=skip_reason, message=message
            )
            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
        return results

    async def _execute_project_sync_actions(
        self,
        project_sync_actions: list[SetupAction],
        project_environments: dict[str, ProjectEnvironment] | None,
        working_dir: Path,
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Execute PROJECT_SYNC actions sequentially.

        Each action invokes the resolved project-environment plugin's
        :meth:`~ProjectEnvironment.sync` method in the manifest directory.

        Args:
            project_sync_actions: The project sync actions.
            project_environments: Dict of project-environment plugins.
            working_dir: Working directory (manifest location).
            parameters: Setup parameters (dry-run, etc.).
            event_queue: Optional queue for progress events.

        Returns:
            List of action results.
        """
        results: list[SetupActionResult] = []

        for action in project_sync_actions:
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

            result = await self._execute_project_sync(action, project_environments, working_dir, parameters)

            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            if not result.success and parameters.fail_fast:
                logger.error(f'Project sync failed: {action.description} - {result.message}')
                break

        return results

    @staticmethod
    async def _execute_project_sync(
        action: SetupAction,
        project_environments: dict[str, ProjectEnvironment] | None,
        working_dir: Path,
        parameters: SetupParameters,
    ) -> SetupActionResult:
        """Execute a single PROJECT_SYNC action.

        Args:
            action: The project sync action.
            project_environments: Dict of project-environment plugins.
            working_dir: Working directory.
            parameters: Setup parameters.

        Returns:
            The result of the sync operation.
        """
        proj_envs = project_environments or {}
        if action.installer is None or action.installer not in proj_envs:
            return SetupActionResult(
                action=action, success=False, message=f"Project environment '{action.installer}' is not available"
            )

        proj_env = proj_envs[action.installer]
        params = ProjectSyncParameters(directory=working_dir, dry=parameters.dry_run)

        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, proj_env.sync, params)
            if success:
                return SetupActionResult(action=action, success=True, message=f'Synced project via {action.installer}')
            return SetupActionResult(
                action=action, success=False, message=f'Project sync failed via {action.installer}'
            )
        except Exception as e:
            return SetupActionResult(action=action, success=False, message=str(e))

    async def _execute_scm_actions(
        self,
        scm_actions: list[SetupAction],
        scm_environments: dict[str, ScmEnvironment] | None,
        working_dir: Path,
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Execute SCM_CLONE actions sequentially.

        Each action invokes the resolved SCM-environment plugin's
        :meth:`~ScmEnvironment.clone` method.

        Args:
            scm_actions: The SCM clone actions.
            scm_environments: Dict of SCM-environment plugins.
            working_dir: Working directory (manifest location).
            parameters: Setup parameters (dry-run, etc.).
            event_queue: Optional queue for progress events.

        Returns:
            List of action results.
        """
        results: list[SetupActionResult] = []

        for action in scm_actions:
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))

            result = await self._execute_scm_clone(action, scm_environments, working_dir, parameters)

            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            if not result.success and not result.skipped and parameters.fail_fast:
                logger.error(f'SCM clone failed: {action.description} - {result.message}')
                break

        return results

    @staticmethod
    async def _execute_scm_clone(
        action: SetupAction,
        scm_environments: dict[str, ScmEnvironment] | None,
        working_dir: Path,
        parameters: SetupParameters,
    ) -> SetupActionResult:
        """Execute a single SCM_CLONE action.

        Args:
            action: The SCM clone action.
            scm_environments: Dict of SCM-environment plugins.
            working_dir: Working directory (manifest location).
            parameters: Setup parameters.

        Returns:
            The result of the clone operation.
        """
        scm_envs = scm_environments or {}
        if action.installer is None or action.installer not in scm_envs:
            return SetupActionResult(
                action=action, success=False, message=f"SCM environment '{action.installer}' is not available"
            )

        if action.package is None:
            return SetupActionResult(action=action, success=False, message='No repository URL specified')

        scm_env = scm_envs[action.installer]
        url = action.package.name

        # Derive destination from the repo URL (last path segment, minus .git)
        repo_name = url.rstrip('/').rsplit('/', 1)[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        destination = working_dir / repo_name

        # Skip if already cloned
        if scm_env.is_cloned(url, destination):
            return SetupActionResult(
                action=action,
                success=True,
                skipped=True,
                skip_reason=SkipReason.ALREADY_INSTALLED,
                message=f"Repository already cloned at '{destination}'",
            )

        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, lambda: scm_env.clone(url, destination, dry=parameters.dry_run))
            if success:
                return SetupActionResult(action=action, success=True, message=f"Cloned '{url}' via {action.installer}")
            return SetupActionResult(
                action=action, success=False, message=f"Clone failed for '{url}' via {action.installer}"
            )
        except Exception as e:
            return SetupActionResult(action=action, success=False, message=str(e))

    async def execute_stream(
        self,
        previews: BatchSetupResults,
        parameters: SetupParameters,
    ) -> AsyncIterator[ProgressEvent]:
        """Stream progress events while executing setup actions for multiple manifests.

        Yields ``ProgressEvent`` items as actions start, complete, and report
        sub-action detail.  Cancellation is handled via standard
        ``task.cancel()`` on the consuming task.

        Args:
            previews: The batch preview results containing actions per manifest.
            parameters: The setup parameters.

        Yields:
            ProgressEvent for each action lifecycle transition and sub-action update.
        """
        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()

        async def _run() -> None:
            """Execute all manifests, emitting events into *queue*."""
            try:
                for preview in previews.manifest_results:
                    if preview.manifest_path is None:
                        continue

                    await self._execute_single(
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
