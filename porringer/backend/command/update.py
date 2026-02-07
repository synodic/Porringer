"""The update command module."""

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

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.core.plugin_schema.environment import Environment, PackageParameters
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
    SetupMode,
    SetupParameters,
    SetupResults,
    SubActionProgress,
)
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError, PluginError
from porringer.utility.utility import canonicalize_type

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CommandExecutionContext:
    """Shared context for command action execution."""

    available_plugins: set[str]
    environments: dict[str, Environment]
    working_dir: Path
    parameters: SetupParameters
    event_queue: asyncio.Queue[ProgressEvent | None] | None


class UpdateCommands:
    """Update commands for downloading updates and setting up from manifests."""

    def __init__(self, cache_manager: DirectoryCacheManager | None = None) -> None:
        """Initialize the UpdateCommands class.

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

        manifest = UpdateCommands._load_manifest_for_validation(path, _error)
        if manifest is None:
            return ManifestValidationResult(diagnostics=diagnostics)

        UpdateCommands._validate_schema_version(manifest, _error)

        available_plugins = UpdateCommands._safe_available_plugins()
        UpdateCommands._validate_plugins(manifest, available_plugins, _error)
        UpdateCommands._validate_package_names(manifest, _warning)
        UpdateCommands._validate_duplicate_packages(manifest, _warning)

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
            _, manifest = UpdateCommands._find_manifest(path)
        except ManifestError as exc:
            msg = str(exc)
            code = UpdateCommands._map_manifest_error_code(msg)
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
            return set(UpdateCommands._get_available_environments().keys())
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
    def _validate_plugins(
        manifest: SetupManifest,
        available_plugins: set[str],
        error_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate plugin names in prerequisites and packages."""
        for i, prereq in enumerate(manifest.prerequisites):
            if prereq.plugin not in available_plugins:
                error_callback(
                    f'prerequisites[{i}].plugin',
                    f"Plugin '{prereq.plugin}' is not installed or recognized",
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )

        for plugin_name in manifest.packages:
            if plugin_name not in available_plugins:
                error_callback(
                    f'packages.{plugin_name}',
                    f"Plugin '{plugin_name}' is not installed or recognized",
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )

    @staticmethod
    def _validate_package_names(
        manifest: SetupManifest,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Validate package specifiers in a manifest."""
        for plugin_name, packages in manifest.packages.items():
            for j, spec in enumerate(packages):
                try:
                    Requirement(str(spec.name))
                except InvalidRequirement as exc:
                    warning_callback(
                        f'packages.{plugin_name}[{j}].name',
                        f"Invalid package specifier '{spec.name}': {exc}",
                        ManifestValidationCode.INVALID_PACKAGE_NAME,
                    )

    @staticmethod
    def _validate_duplicate_packages(
        manifest: SetupManifest,
        warning_callback: Callable[[str, str, ManifestValidationCode], None],
    ) -> None:
        """Warn when packages appear under multiple plugins."""
        seen: dict[str, list[str]] = {}
        for plugin_name, packages in manifest.packages.items():
            for spec in packages:
                canonical = str(canonicalize_name(spec.name.name))
                seen.setdefault(canonical, []).append(plugin_name)

        for pkg_name, plugins in seen.items():
            if len(plugins) > 1:
                warning_callback(
                    'packages',
                    f"Package '{pkg_name}' is listed under multiple plugins: {', '.join(plugins)}",
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
            return UpdateCommands._load_manifest_file(path)

        if path.is_dir():
            # Try porringer.json first, then pyproject.toml
            porringer_file = path / 'porringer.json'
            if porringer_file.exists():
                return UpdateCommands._load_manifest_file(porringer_file)

            pyproject_file = path / 'pyproject.toml'
            if pyproject_file.exists():
                return UpdateCommands._load_pyproject_manifest(pyproject_file)

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
            return UpdateCommands._load_pyproject_manifest(path)

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
    def _get_cli_command(
        action: SetupAction, environments: dict[str, Environment], mode: SetupMode = SetupMode.INSTALL
    ) -> list[str]:
        """Gets the CLI command string for an action.

        Args:
            action: The action to get the command for.
            environments: Dict of instantiated environment plugins.
            mode: The execution mode (determines install vs upgrade command).

        Returns:
            The CLI command as a list of strings, or empty list if not applicable.
        """
        match action.action_type:
            case SetupActionType.CHECK_PLUGIN:
                # No CLI command for plugin checks
                return []
            case SetupActionType.PACKAGE:
                if action.plugin and action.package and action.plugin in environments:
                    env = environments[action.plugin]
                    if mode in {SetupMode.UPGRADE, SetupMode.ENSURE}:
                        return env.upgrade_command(action.package)
                    return env.install_command(action.package)
                return []
            case SetupActionType.RUN_COMMAND:
                return action.command or []
            case _:
                return []

    @staticmethod
    def _build_actions(manifest: SetupManifest, mode: SetupMode = SetupMode.INSTALL) -> list[SetupAction]:
        """Builds the list of actions from a manifest.

        All package entries become ``PACKAGE`` actions. The ``mode`` parameter
        controls only the human-readable description verb; the execution layer
        uses the mode on ``SetupParameters`` to decide install-vs-upgrade
        behaviour at runtime.

        Args:
            manifest: The parsed setup manifest.
            mode: The execution mode (used for description text).

        Returns:
            List of actions to perform.
        """
        actions: list[SetupAction] = []

        # Add prerequisite check actions
        for prereq in manifest.prerequisites:
            if not prereq.is_applicable():
                continue
            actions.append(
                SetupAction(
                    action_type=SetupActionType.CHECK_PLUGIN,
                    description=f"Check plugin '{prereq.plugin}' is available",
                    plugin=prereq.plugin,
                )
            )

        # Determine description verb based on mode
        verb_map = {SetupMode.INSTALL: 'Install', SetupMode.UPGRADE: 'Upgrade', SetupMode.ENSURE: 'Ensure'}
        verb = verb_map[mode]

        # Add package actions
        for plugin_name, packages in manifest.packages.items():
            for package in packages:
                if not package.is_applicable():
                    continue
                actions.append(
                    SetupAction(
                        action_type=SetupActionType.PACKAGE,
                        description=f"{verb} '{package.name}' via {plugin_name}",
                        plugin=plugin_name,
                        package=package.name,
                        package_description=package.description,
                    )
                )

        # Add post-install command actions
        for command_str in manifest.post_install:
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
    def preview_single(path: Path, mode: SetupMode = SetupMode.INSTALL) -> SetupResults:
        """Previews the setup actions for a single path without executing them.

        Args:
            path: Path to manifest file or directory containing one.
            mode: The execution mode (install, upgrade, or ensure).

        Returns:
            SetupResults containing the list of actions that would be performed.

        Raises:
            ManifestError: If the manifest cannot be found or parsed.
        """
        logger.info(f'Previewing setup from: {path}')

        manifest_path, manifest = UpdateCommands._find_manifest(path)
        actions = UpdateCommands._build_actions(manifest, mode)
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
                result = self.preview_single(path, mode=parameters.mode)
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
        mode: SetupMode = SetupMode.INSTALL,
    ) -> SetupActionResult:
        """Simulates executing an action in dry-run mode.

        For CHECK_PLUGIN and PACKAGE actions, real system state is checked
        so that the result accurately reflects whether the action would be skipped.

        Args:
            action: The action to simulate.
            available_plugins: Set of available plugin names.
            environments: Dict of instantiated environment plugins.
            mode: The execution mode (affects skip logic for packages).

        Returns:
            The simulated result.
        """
        if action.action_type == SetupActionType.CHECK_PLUGIN:
            return UpdateCommands._dry_run_check_plugin(action, available_plugins)
        if action.action_type == SetupActionType.PACKAGE:
            return UpdateCommands._dry_run_package_action(action, environments, mode)
        if action.action_type == SetupActionType.RUN_COMMAND:
            return SetupActionResult(action=action, success=True)
        return SetupActionResult(action=action, success=False, message=f'Unknown action type: {action.action_type}')

    @staticmethod
    def _dry_run_check_plugin(action: SetupAction, available_plugins: set[str]) -> SetupActionResult:
        """Simulate a plugin availability check."""
        if action.plugin is None:
            return SetupActionResult(action=action, success=False, message='No plugin specified')
        if action.plugin in available_plugins:
            return SetupActionResult(action=action, success=True, skipped=True)
        return SetupActionResult(
            action=action,
            success=False,
            message=f"Required plugin '{action.plugin}' is not available",
        )

    @staticmethod
    def _dry_run_package_action(
        action: SetupAction,
        environments: dict[str, Environment],
        mode: SetupMode,
    ) -> SetupActionResult:
        """Simulate a package action in dry-run mode."""
        if action.plugin is None or action.package is None or action.plugin not in environments:
            return SetupActionResult(action=action, success=True)

        try:
            installed_packages = environments[action.plugin].packages()
            is_installed, skip_reason = UpdateCommands._is_package_installed(action.package, installed_packages)
        except PluginError as e:
            logger.debug(f'Dry-run: plugin error checking packages for {action.plugin}: {e}')
            return SetupActionResult(action=action, success=True)
        except Exception as e:
            logger.debug(f'Dry-run: could not check installed packages for {action.plugin}: {e}')
            return SetupActionResult(action=action, success=True)

        if mode == SetupMode.INSTALL:
            if is_installed:
                logger.info(f"Dry-run: skipping '{action.package}': {skip_reason}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=skip_reason,
                )
        elif not is_installed:
            return SetupActionResult(
                action=action,
                success=True,
                skip_reason='not installed, will install instead',
            )

        return SetupActionResult(action=action, success=True)

    @staticmethod
    def _execute_check_plugin(action: SetupAction, available_plugins: set[str]) -> SetupActionResult:
        """Executes a plugin availability check.

        Args:
            action: The check action.
            available_plugins: Set of available plugin names.

        Returns:
            The result of the check.
        """
        if action.plugin is None:
            return SetupActionResult(action=action, success=False, message='No plugin specified')

        if action.plugin in available_plugins:
            logger.info(f"Plugin '{action.plugin}' is available")
            # Mark as skipped since the plugin was found - no need to display this
            return SetupActionResult(action=action, success=True, skipped=True)
        else:
            message = f"Required plugin '{action.plugin}' is not available"
            logger.error(message)
            return SetupActionResult(action=action, success=False, message=message)

    @staticmethod
    def _is_package_installed(
        package: PackageRef,
        installed_packages: list[Package],
    ) -> tuple[bool, str | None]:
        """Checks if a package is already installed with a compatible version.

        Args:
            package: The package reference
            installed_packages: List of installed packages from the environment

        Returns:
            Tuple of (is_installed, skip_reason or None)
        """
        canonical_name = canonicalize_name(package.name)
        for installed in installed_packages:
            if canonicalize_name(installed.name) == canonical_name:
                if not package.constraint:
                    return True, f'{installed.name}=={installed.version} already installed'
                if installed.version is not None:
                    try:
                        req = Requirement(str(package))
                        if Version(installed.version) in req.specifier:
                            return True, f'{installed.name}=={installed.version} satisfies {package}'
                    except InvalidVersion, InvalidRequirement:
                        pass
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
        mode: SetupMode,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupActionResult:
        """Execute a package install or upgrade based on the mode.

        In INSTALL mode, skips already-installed packages.
        In UPGRADE/ENSURE mode, upgrades installed packages and falls back to
        install for packages that are not yet present.

        Args:
            action: The package action.
            environments: Dict of instantiated environment plugins.
            mode: The execution mode.
            event_queue: Optional queue to emit sub-action events into.

        Returns:
            The result of the operation.
        """
        if action.plugin is None or action.package is None:
            return SetupActionResult(action=action, success=False, message='Plugin or package not specified')

        if action.plugin not in environments:
            return SetupActionResult(action=action, success=False, message=f"Plugin '{action.plugin}' is not available")

        environment = environments[action.plugin]

        # Check if package is already installed
        is_installed = False
        skip_reason: str | None = None
        try:
            loop = asyncio.get_running_loop()
            installed_packages = await loop.run_in_executor(None, environment.packages)
            is_installed, skip_reason = UpdateCommands._is_package_installed(action.package, installed_packages)
        except PluginError as e:
            logger.debug(f'Plugin error checking packages for {action.plugin}: {e}')
        except Exception as e:
            logger.debug(f'Could not check installed packages for {action.plugin}: {e}')

        if mode == SetupMode.INSTALL:
            if is_installed:
                logger.info(f"Skipping '{action.package}': {skip_reason}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=skip_reason,
                )
            logger.info(f"Installing '{action.package}' via {action.plugin}")
            return await self._attempt_package_operation(action, environment, SetupMode.INSTALL, event_queue)
        else:
            # UPGRADE or ENSURE
            if not is_installed:
                logger.info(f"'{action.package}' not installed via {action.plugin}, falling back to install")
                return await self._attempt_package_operation(action, environment, SetupMode.INSTALL, event_queue)
            logger.info(f"Upgrading '{action.package}' via {action.plugin}")
            return await self._attempt_package_operation(action, environment, mode, event_queue)

    @staticmethod
    async def _attempt_package_operation(
        action: SetupAction,
        environment: Environment,
        mode: SetupMode,
        event_queue: asyncio.Queue[ProgressEvent | None] | None = None,
    ) -> SetupActionResult:
        """Attempt to install or upgrade a package via the given environment plugin.

        Args:
            action: The package action.
            environment: The environment plugin to use.
            mode: Whether to install or upgrade.
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

        is_install = mode == SetupMode.INSTALL
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

    async def _execute_check_actions(
        self,
        check_actions: list[SetupAction],
        available_plugins: set[str],
        environments: dict[str, Environment],
        parameters: SetupParameters,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Execute CHECK_PLUGIN actions sequentially.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        results: list[SetupActionResult] = []
        for action in check_actions:
            if parameters.dry_run:
                result = self._dry_run_action(action, available_plugins, environments, parameters.mode)
            else:
                result = self._execute_check_plugin(action, available_plugins)
            results.append(result)
            if event_queue is not None:
                event_queue.put_nowait(ProgressEvent(kind=ProgressEventKind.ACTION_STARTED, action=action))
                event_queue.put_nowait(
                    ProgressEvent(kind=ProgressEventKind.ACTION_COMPLETED, action=action, result=result)
                )
            if not result.success and not result.skipped:
                logger.error(f'Action failed: {action.description} - {result.message}')
                if parameters.fail_fast:
                    return results, False
        return results, True

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
                    package_actions, available_plugins, environments, parameters.mode, event_queue
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
        mode: SetupMode,
        event_queue: asyncio.Queue[ProgressEvent | None] | None,
    ) -> list[SetupActionResult]:
        """Execute dry-run for package actions."""
        results: list[SetupActionResult] = []
        for action in package_actions:
            result = self._dry_run_action(action, available_plugins, environments, mode)
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
            if action.plugin and action.plugin in environments and environments[action.plugin].supports_parallel():
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
            result = await self._execute_package(action, environments, parameters.mode, event_queue)
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
                result = await self._execute_package(action, environments, parameters.mode, event_queue)
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
                    context.parameters.mode,
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

        Package operations are executed in parallel when plugins support it.
        CHECK_PLUGIN and RUN_COMMAND actions are executed sequentially.

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

        environments = self._get_available_environments()
        available_plugins = set(environments.keys())
        working_dir = path if path.is_dir() else path.parent

        # Populate CLI commands for all actions
        for action in actions:
            action.cli_command = UpdateCommands._get_cli_command(action, environments, parameters.mode)

        # Separate actions by type
        check_actions = [a for a in actions if a.action_type == SetupActionType.CHECK_PLUGIN]
        package_actions = [a for a in actions if a.action_type == SetupActionType.PACKAGE]
        command_actions = [a for a in actions if a.action_type == SetupActionType.RUN_COMMAND]

        results: list[SetupActionResult] = []

        # Execute CHECK_PLUGIN actions
        check_results, should_continue = await self._execute_check_actions(
            check_actions, available_plugins, environments, parameters, event_queue
        )
        results.extend(check_results)
        if not should_continue:
            return SetupResults(actions=actions, results=results)

        # Execute PACKAGE actions
        if package_actions:
            package_results, should_continue = await self._execute_package_actions(
                package_actions,
                environments,
                available_plugins,
                parameters,
                event_queue,
            )
            results.extend(package_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)

        # Execute RUN_COMMAND actions
        command_context = _CommandExecutionContext(
            available_plugins=available_plugins,
            environments=environments,
            working_dir=working_dir,
            parameters=parameters,
            event_queue=event_queue,
        )
        command_results = await self._execute_command_actions(command_actions, command_context)
        results.extend(command_results)

        return SetupResults(actions=actions, results=results)

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
