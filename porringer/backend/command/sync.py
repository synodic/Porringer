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
from porringer.core.schema import Package, PackageRef
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
    SetupActionType,
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


@dataclass(frozen=True)
class _ExecutionPhaseContext:
    """Shared context for execution phase operations."""

    environments: dict[str, Environment]
    project_environments: dict[str, ProjectEnvironment] | None
    available_plugins: set[str]
    parameters: SetupParameters
    skip_project: bool
    working_dir: Path
    event_queue: asyncio.Queue[ProgressEvent | None] | None


@dataclass(frozen=True)
class _CommandExecutionContext:
    """Shared context for command action execution."""

    available_plugins: set[str]
    environments: dict[str, Environment]
    working_dir: Path
    parameters: SetupParameters
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

        available_plugins = SyncCommands._safe_available_plugins()
        SyncCommands._validate_backends(manifest, available_plugins, _error)
        SyncCommands._validate_package_names(manifest, _warning)
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
    def _safe_available_plugins() -> set[str]:
        """Load available plugins, returning an empty set on failure."""
        try:
            return set(SyncCommands._get_available_environments().keys())
        except Exception:
            return set()

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
        available_plugins: set[str],
        error_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate that each backend in ``state`` can be resolved to a plugin."""
        environments = SyncCommands._get_available_environments()
        project_environments = SyncCommands._get_available_project_environments()
        resolver = BackendResolver(environments, manifest.preferences, project_environments)

        for backend_name in manifest.state:
            installer = resolver.resolve(backend_name)
            if installer is None:
                error_callback(
                    f'state.{backend_name}',
                    f"No available installer for backend '{backend_name}'",
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )

    # Backends whose package names conform to PEP 508 / PEP 440.
    _PEP440_BACKENDS: frozenset[str] = frozenset(
        {
            'python',
            'python-tool',
            'python-runtime',
            'python-project',
        }
    )

    @staticmethod
    def _validate_package_names(
        manifest: SetupManifest,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate package specifiers in a manifest.

        PEP 440 validation is only applied to Python-ecosystem backends.
        Non-Python backends (node, deno, system, …) accept any non-empty
        package name since their naming conventions differ.
        """
        for backend_name, packages in manifest.state.items():
            for j, spec in enumerate(packages):
                name_str = str(spec.name)
                if not name_str.strip():
                    warning_callback(
                        f'state.{backend_name}[{j}].name',
                        'Empty package name',
                        ManifestValidationCode.INVALID_PACKAGE_NAME,
                    )
                    continue
                if backend_name in SyncCommands._PEP440_BACKENDS:
                    try:
                        Requirement(name_str)
                    except InvalidRequirement as exc:
                        warning_callback(
                            f'state.{backend_name}[{j}].name',
                            f"Invalid package specifier '{spec.name}': {exc}",
                            ManifestValidationCode.INVALID_PACKAGE_NAME,
                        )

    @staticmethod
    def _validate_duplicate_packages(
        manifest: SetupManifest,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Warn when packages appear under multiple backends."""
        seen: dict[str, list[str]] = {}
        for backend_name, packages in manifest.state.items():
            for spec in packages:
                canonical = str(canonicalize_name(spec.name.name))
                seen.setdefault(canonical, []).append(backend_name)

        for pkg_name, backends in seen.items():
            if len(backends) > 1:
                warning_callback(
                    'state',
                    f"Package '{pkg_name}' is listed under multiple backends: {', '.join(backends)}",
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
    def _get_available_environments() -> dict[str, Environment]:
        """Gets all available environment plugins as a dict.

        Returns:
            Dict mapping plugin name to instantiated environment.
        """
        builder = Builder()
        plugin_infos = builder.find_environments()
        environments = builder.build_environments(plugin_infos)

        result: dict[str, Environment] = {}
        for env in environments:
            canonicalized = canonicalize_type(type(env))
            result[canonicalized.name] = env

        return result

    @staticmethod
    def _get_available_project_environments() -> dict[str, ProjectEnvironment]:
        """Gets all available project-environment plugins as a dict.

        Returns:
            Dict mapping plugin name to instantiated project environment.
        """
        builder = Builder()
        plugin_infos = builder.find_project_environments()
        project_environments = builder.build_project_environments(plugin_infos)

        result: dict[str, ProjectEnvironment] = {}
        for proj_env in project_environments:
            canonicalized = canonicalize_type(type(proj_env))
            result[canonicalized.name] = proj_env

        return result

    @staticmethod
    def _get_cli_command(
        action: SetupAction,
        environments: dict[str, Environment],
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
        project_environments: dict[str, ProjectEnvironment] | None = None,
    ) -> list[str]:
        """Gets the CLI command string for an action.

        Args:
            action: The action to get the command for.
            environments: Dict of instantiated environment plugins.
            strategy: The sync strategy (determines install vs upgrade command).
            project_environments: Dict of project-environment plugins.

        Returns:
            The CLI command as a list of strings, or empty list if not applicable.
        """
        cmd: list[str] = []
        match action.action_type:
            case SetupActionType.PACKAGE:
                if action.installer and action.package and action.installer in environments:
                    env = environments[action.installer]
                    if strategy in {SyncStrategy.LATEST, SyncStrategy.EXACT}:
                        cmd = env.upgrade_command(action.package)
                    else:
                        cmd = env.install_command(action.package)
            case SetupActionType.PROJECT_SYNC:
                proj_envs = project_environments or {}
                if action.installer and action.installer in proj_envs:
                    cmd = proj_envs[action.installer].sync_command()
            case SetupActionType.RUN_COMMAND:
                cmd = action.command or []
        return cmd

    @staticmethod
    def _build_actions(
        manifest: SetupManifest,
        environments: dict[str, Environment],
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
        project_environments: dict[str, ProjectEnvironment] | None = None,
    ) -> list[SetupAction]:
        """Builds the list of actions from a manifest.

        All package entries become ``PACKAGE`` actions.  Backends that
        resolve to a :class:`ProjectEnvironment` plugin produce a single
        ``PROJECT_SYNC`` action instead.  The ``strategy`` parameter
        controls only the human-readable description verb; the execution
        layer uses the strategy on ``SetupParameters`` to decide
        install-vs-upgrade behaviour at runtime.

        Each backend key in ``manifest.state`` is resolved to an installer
        plugin via :class:`BackendResolver`.

        Args:
            manifest: The parsed setup manifest.
            environments: Dict of instantiated environment plugins.
            strategy: The sync strategy (used for description text).
            project_environments: Dict of project-environment plugins.

        Returns:
            List of actions to perform.
        """
        actions: list[SetupAction] = []
        proj_envs = project_environments or {}

        resolver = BackendResolver(environments, manifest.preferences, proj_envs)

        # Determine description verb based on strategy
        verb_map = {
            SyncStrategy.MINIMAL: 'Install',
            SyncStrategy.LATEST: 'Upgrade',
            SyncStrategy.EXACT: 'Ensure',
        }
        verb = verb_map[strategy]

        # Add package actions — resolve each backend to an installer
        for backend_name, packages in manifest.state.items():
            installer = resolver.resolve(backend_name)
            if installer is None:
                logger.warning("No installer available for backend '%s'; skipping its packages", backend_name)
                continue

            # Project-environment backends produce a single PROJECT_SYNC action
            if installer in proj_envs:
                actions.append(
                    SetupAction(
                        action_type=SetupActionType.PROJECT_SYNC,
                        description=f'Sync project via {installer}',
                        backend=backend_name,
                        installer=installer,
                    )
                )
                continue

            for package in packages:
                if not package.is_applicable():
                    continue
                actions.append(
                    SetupAction(
                        action_type=SetupActionType.PACKAGE,
                        description=f"{verb} '{package.name}' via {installer}",
                        backend=backend_name,
                        installer=installer,
                        package=package.name,
                        package_description=package.description,
                    )
                )

        # Add post-sync command actions
        for command_str in manifest.post_sync:
            command_parts = shlex.split(command_str)
            actions.append(
                SetupAction(
                    action_type=SetupActionType.RUN_COMMAND,
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
        environments = SyncCommands._get_available_environments()
        project_environments = SyncCommands._get_available_project_environments()
        actions = SyncCommands._build_actions(manifest, environments, strategy, project_environments)
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
        available_plugins: set[str],
        environments: dict[str, Environment],
        strategy: SyncStrategy = SyncStrategy.MINIMAL,
    ) -> SetupActionResult:
        """Simulates executing an action in dry-run mode.

        For PACKAGE actions, real system state is checked so that the result
        accurately reflects whether the action would be skipped.

        Args:
            action: The action to simulate.
            available_plugins: Set of available plugin names.
            environments: Dict of instantiated environment plugins.
            strategy: The sync strategy (affects skip logic for packages).

        Returns:
            The simulated result.
        """
        if action.action_type == SetupActionType.PACKAGE:
            return SyncCommands._dry_run_package_action(action, environments, strategy)
        if action.action_type == SetupActionType.PROJECT_SYNC:
            return SetupActionResult(action=action, success=True)
        if action.action_type == SetupActionType.RUN_COMMAND:
            return SetupActionResult(action=action, success=True)
        return SetupActionResult(action=action, success=False, message=f'Unknown action type: {action.action_type}')

    @staticmethod
    def _dry_run_package_action(
        action: SetupAction,
        environments: dict[str, Environment],
        strategy: SyncStrategy,
    ) -> SetupActionResult:
        """Simulate a package action in dry-run mode."""
        if action.installer is None or action.package is None or action.installer not in environments:
            return SetupActionResult(action=action, success=True)

        try:
            installed_packages = environments[action.installer].packages()
            is_installed, installed_detail = SyncCommands._is_package_installed(
                action.package, installed_packages, action.backend
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
        backend: str | None = None,
    ) -> tuple[bool, str | None]:
        """Checks if a package is already installed with a compatible version.

        For Python-ecosystem backends, uses PEP 440 canonicalization and
        specifier matching.  For other backends, uses case-insensitive name
        comparison and simple string version equality.

        Args:
            package: The package reference
            installed_packages: List of installed packages from the environment
            backend: The backend identifier (used to select matching strategy)

        Returns:
            Tuple of (is_installed, skip_reason or None)
        """
        is_pep440 = backend in SyncCommands._PEP440_BACKENDS if backend else True

        for installed in installed_packages:
            if is_pep440:
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
        try:
            loop = asyncio.get_running_loop()
            installed_packages = await loop.run_in_executor(None, environment.packages)
            is_installed, installed_detail = SyncCommands._is_package_installed(
                action.package, installed_packages, action.backend
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
        available_plugins: set[str],
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Execute PACKAGE actions with parallel support.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        if parameters.dry_run:
            return (
                self._dry_run_package_actions(
                    package_actions, available_plugins, environments, parameters.strategy, event_queue
                ),
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
        available_plugins: set[str],
        environments: dict[str, Environment],
        strategy: SyncStrategy,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Execute dry-run for package actions."""
        results: list[SetupActionResult] = []
        for action in package_actions:
            result = self._dry_run_action(action, available_plugins, environments, strategy)
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
        context: _CommandExecutionContext,
    ) -> list[SetupActionResult]:
        """Execute RUN_COMMAND actions sequentially."""
        results: list[SetupActionResult] = []
        for action in command_actions:
            if context.parameters.dry_run:
                result = self._dry_run_action(
                    action,
                    context.available_plugins,
                    context.environments,
                    context.parameters.strategy,
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

    async def _execute_single(
        self,
        actions: list[SetupAction],
        path: Path,
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupResults:
        """Execute setup actions for a single path with parallel support.

        Execution is **phased**: actions targeting the ``python-runtime``
        backend run first so that the resolved interpreter path can be
        forwarded to downstream ``python`` / ``python-tool`` installers.

        Package operations are executed in parallel when plugins support it.
        RUN_COMMAND actions are executed sequentially after all packages.

        Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.

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
        environments = self._get_available_environments()
        project_environments = self._get_available_project_environments()
        available_plugins = set(environments.keys()) | set(project_environments.keys())

        skip_project = parameters.project_directory is False
        if isinstance(parameters.project_directory, Path):
            working_dir = parameters.project_directory
        else:
            working_dir = path if path.is_dir() else path.parent

        # Populate CLI commands for all actions
        for action in actions:
            action.cli_command = SyncCommands._get_cli_command(
                action, environments, parameters.strategy, project_environments
            )

        # Separate actions by type
        package_actions = [a for a in actions if a.action_type == SetupActionType.PACKAGE]
        project_sync_actions = [a for a in actions if a.action_type == SetupActionType.PROJECT_SYNC]
        command_actions = [a for a in actions if a.action_type == SetupActionType.RUN_COMMAND]

        # --- Phase 1: runtime-provider actions (pim / pyenv) ---------------
        runtime_actions = [
            a for a in package_actions if a.installer and isinstance(environments.get(a.installer), RuntimeProvider)
        ]

        if runtime_actions:
            runtime_results, should_continue = await self._execute_package_actions(
                runtime_actions,
                environments,
                available_plugins,
                parameters,
                event_queue,
            )
            results.extend(runtime_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)
            self._propagate_runtime(runtime_actions, environments, project_environments)

        # --- Phase 2: remaining package actions --------------------------
        dependent_actions = [a for a in package_actions if a not in runtime_actions]
        if dependent_actions:
            package_results, should_continue = await self._execute_package_actions(
                dependent_actions,
                environments,
                available_plugins,
                parameters,
                event_queue,
            )
            results.extend(package_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)

        # --- Phase 2.5 & 3: project sync and command phases ---------------
        if project_sync_actions:
            results.extend(
                await self._execute_project_sync_phase(
                    project_sync_actions,
                    _ExecutionPhaseContext(
                        environments=environments,
                        project_environments=project_environments,
                        available_plugins=available_plugins,
                        parameters=parameters,
                        skip_project=skip_project,
                        working_dir=working_dir,
                        event_queue=event_queue,
                    ),
                )
            )

        if command_actions:
            results.extend(
                await self._execute_command_phase(
                    command_actions,
                    _ExecutionPhaseContext(
                        environments=environments,
                        project_environments=project_environments,
                        available_plugins=available_plugins,
                        parameters=parameters,
                        skip_project=skip_project,
                        working_dir=working_dir,
                        event_queue=event_queue,
                    ),
                )
            )

        return SetupResults(actions=actions, results=results)

    async def _execute_project_sync_phase(
        self,
        project_sync_actions: list[SetupAction],
        context: _ExecutionPhaseContext,
    ) -> list[SetupActionResult]:
        """Execute or skip project-sync actions based on context."""
        if not context.skip_project:
            return await self._execute_project_sync_actions(
                project_sync_actions,
                context.project_environments,
                context.working_dir,
                context.parameters,
                context.event_queue,
            )
        return self._skip_actions(
            project_sync_actions,
            SkipReason.NO_PROJECT_DIRECTORY,
            'No project directory provided',
            context.event_queue,
        )

    async def _execute_command_phase(
        self,
        command_actions: list[SetupAction],
        context: _ExecutionPhaseContext,
    ) -> list[SetupActionResult]:
        """Execute or skip command actions based on context."""
        if not context.skip_project:
            cmd_context = _CommandExecutionContext(
                available_plugins=context.available_plugins,
                environments=context.environments,
                working_dir=context.working_dir,
                parameters=context.parameters,
                event_queue=context.event_queue,
            )
            return await self._execute_command_actions(command_actions, cmd_context)
        return self._skip_actions(
            command_actions,
            SkipReason.NO_PROJECT_DIRECTORY,
            'No project directory for post-sync command',
            context.event_queue,
        )

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

            # Propagate to environment plugins that consume this runtime kind
            for name, downstream in environments.items():
                if isinstance(downstream, RuntimeConsumer):
                    downstream_type = cast(type[RuntimeConsumer], type(downstream))
                    if downstream_type.consumed_runtime_kind() == kind:
                        downstream.runtime_executable = executable
                        logger.debug('Set runtime_executable on %s to %s', name, executable)

            # Propagate to project-environment plugins that consume this runtime kind
            for name, proj_downstream in proj_envs.items():
                is_consumer = isinstance(proj_downstream, RuntimeConsumer)
                if is_consumer:
                    proj_type = cast(type[RuntimeConsumer], type(proj_downstream))
                    if proj_type.consumed_runtime_kind() == kind:
                        proj_downstream.runtime_executable = executable
                        logger.debug('Set runtime_executable on project plugin %s to %s', name, executable)

            # Use only the first successfully resolved runtime
            break

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

            if parameters.dry_run:
                result = SetupActionResult(action=action, success=True)
            else:
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
