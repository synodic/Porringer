"""The update command module."""

from __future__ import annotations

import json
import shlex
import subprocess
import tomllib
from logging import Logger
from pathlib import Path

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.core.plugin_schema.environment import Environment, InstallParameters
from porringer.core.schema import PackageName
from porringer.schema import (
    BatchSetupResults,
    DownloadParameters,
    DownloadResult,
    ProgressCallback,
    SetupAction,
    SetupActionResult,
    SetupActionType,
    SetupManifest,
    SetupParameters,
    SetupResults,
)
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError
from porringer.utility.utility import canonicalize_type


class UpdateCommands:
    """Update commands for downloading updates and setting up from manifests."""

    def __init__(self, logger: Logger, cache_manager: DirectoryCacheManager | None = None) -> None:
        """Initialize the UpdateCommands class.

        Args:
            logger: Logger instance for logging actions.
            cache_manager: Optional cache manager for resolving cached paths.
        """
        self.logger = logger
        self._cache_manager = cache_manager

    def download(
        self,
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
        self.logger.info(f'Downloading: {parameters.url}')

        return download_file(parameters, self.logger, progress_callback)

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

    def _get_available_environments(self) -> dict[str, Environment]:
        """Gets all available environment plugins as a dict.

        Returns:
            Dict mapping plugin name to instantiated environment.
        """
        builder = Builder(self.logger)
        plugin_infos = builder.find_environments()
        environments = builder.build_environments(plugin_infos)

        result: dict[str, Environment] = {}
        for env in environments:
            canonicalized = canonicalize_type(type(env))
            result[canonicalized.name] = env

        return result

    @staticmethod
    def _get_cli_command(action: SetupAction, environments: dict[str, Environment]) -> list[str]:
        """Gets the CLI command string for an action.

        Args:
            action: The action to get the command for.
            environments: Dict of instantiated environment plugins.

        Returns:
            The CLI command as a list of strings, or empty list if not applicable.
        """
        match action.action_type:
            case SetupActionType.CHECK_PLUGIN:
                # No CLI command for plugin checks
                return []
            case SetupActionType.INSTALL_PACKAGE:
                if action.plugin and action.package and action.plugin in environments:
                    env = environments[action.plugin]
                    return env.install_command(PackageName(action.package))
                return []
            case SetupActionType.RUN_COMMAND:
                return action.command or []
            case _:
                return []

    @staticmethod
    def _build_actions(manifest: SetupManifest) -> list[SetupAction]:
        """Builds the list of actions from a manifest.

        Args:
            manifest: The parsed setup manifest.

        Returns:
            List of actions to perform.
        """
        actions: list[SetupAction] = []

        # Add prerequisite check actions
        for prereq in manifest.prerequisites:
            actions.append(
                SetupAction(
                    action_type=SetupActionType.CHECK_PLUGIN,
                    description=f"Check plugin '{prereq.plugin}' is available",
                    plugin=prereq.plugin,
                )
            )

        # Add package install actions
        for plugin_name, packages in manifest.packages.items():
            for package in packages:
                actions.append(
                    SetupAction(
                        action_type=SetupActionType.INSTALL_PACKAGE,
                        description=f"Install '{package}' via {plugin_name}",
                        plugin=plugin_name,
                        package=package,
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

    def preview_single(self, path: Path) -> SetupResults:
        """Previews the setup actions for a single path without executing them.

        Args:
            path: Path to manifest file or directory containing one.

        Returns:
            SetupResults containing the list of actions that would be performed.

        Raises:
            ManifestError: If the manifest cannot be found or parsed.
        """
        self.logger.info(f'Previewing setup from: {path}')

        manifest_path, manifest = UpdateCommands._find_manifest(path)
        actions = UpdateCommands._build_actions(manifest)

        return SetupResults(actions=actions, manifest_path=manifest_path)

    def preview_batch(self, parameters: SetupParameters) -> BatchSetupResults:
        """Preview setup actions for multiple paths.

        Args:
            parameters: The setup parameters with paths or group.

        Returns:
            BatchSetupResults containing previews for each manifest.
        """
        paths = self._resolve_paths(parameters)
        self.logger.info(f'Previewing setup for {len(paths)} path(s)')

        manifest_results: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = []

        for path in paths:
            try:
                result = self.preview_single(path)
                manifest_results.append(result)
            except ManifestError as e:
                failed_paths.append((path, str(e.error)))
                if parameters.fail_fast:
                    break

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)

    def execute_single(self, actions: list[SetupAction], path: Path, parameters: SetupParameters) -> SetupResults:
        """Executes setup actions for a single path.

        Args:
            actions: The list of actions to execute (from preview).
            path: The path this execution is for (used for working directory).
            parameters: The setup parameters.

        Returns:
            SetupResults containing the results of each action.
        """
        self.logger.info(f'Executing {len(actions)} setup actions (dry_run={parameters.dry_run})')

        # Get fresh plugin state
        environments = self._get_available_environments()
        available_plugins = set(environments.keys())

        results: list[SetupActionResult] = []
        working_dir = path if path.is_dir() else path.parent

        for action in actions:
            # Populate CLI command for display
            action.cli_command = UpdateCommands._get_cli_command(action, environments)

            if parameters.dry_run:
                # In dry-run mode, just simulate success (but still check plugins)
                result = self._dry_run_action(action, available_plugins)
            else:
                result = self._execute_action(action, environments, available_plugins, working_dir, parameters.timeout)
            results.append(result)

            # Fail fast on error (but not on skipped)
            if not result.success and not result.skipped:
                self.logger.error(f'Action failed: {action.description} - {result.message}')
                if not parameters.dry_run:
                    break

        return SetupResults(actions=actions, results=results)

    @staticmethod
    def _dry_run_action(action: SetupAction, available_plugins: set[str]) -> SetupActionResult:
        """Simulates executing an action in dry-run mode.

        Args:
            action: The action to simulate.
            available_plugins: Set of available plugin names.

        Returns:
            The simulated result.
        """
        match action.action_type:
            case SetupActionType.CHECK_PLUGIN:
                # Still check plugins - they might not be available
                if action.plugin is None:
                    return SetupActionResult(action=action, success=False, message='No plugin specified')
                if action.plugin in available_plugins:
                    return SetupActionResult(action=action, success=True, skipped=True)
                else:
                    return SetupActionResult(
                        action=action, success=False, message=f"Required plugin '{action.plugin}' is not available"
                    )
            case SetupActionType.INSTALL_PACKAGE:
                # Simulate success
                return SetupActionResult(action=action, success=True)
            case SetupActionType.RUN_COMMAND:
                # Simulate success
                return SetupActionResult(action=action, success=True)
            case _:
                return SetupActionResult(
                    action=action, success=False, message=f'Unknown action type: {action.action_type}'
                )

    def execute_batch(self, previews: BatchSetupResults, parameters: SetupParameters) -> BatchSetupResults:
        """Execute setup actions for multiple manifests.

        Args:
            previews: The batch preview results containing actions per manifest.
            parameters: The setup parameters.

        Returns:
            BatchSetupResults containing execution results for each manifest.
        """
        self.logger.info(f'Executing setup for {len(previews.manifest_results)} manifest(s)')

        manifest_results: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = list(previews.failed_paths)

        for preview in previews.manifest_results:
            if preview.manifest_path is None:
                continue

            result = self.execute_single(preview.actions, preview.manifest_path, parameters)
            result.manifest_path = preview.manifest_path
            manifest_results.append(result)

            # Check for failures
            has_failure = any(not r.success for r in result.results)
            if has_failure and parameters.fail_fast:
                break

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)

    def _execute_action(
        self,
        action: SetupAction,
        environments: dict[str, Environment],
        available_plugins: set[str],
        working_dir: Path,
        timeout: int,
    ) -> SetupActionResult:
        """Executes a single setup action.

        Args:
            action: The action to execute.
            environments: Dict of instantiated environment plugins.
            available_plugins: Set of available plugin names.
            working_dir: Working directory for commands.
            timeout: Timeout in seconds for commands.

        Returns:
            The result of executing the action.
        """
        match action.action_type:
            case SetupActionType.CHECK_PLUGIN:
                return self._execute_check_plugin(action, available_plugins)
            case SetupActionType.INSTALL_PACKAGE:
                return self._execute_install_package(action, environments)
            case SetupActionType.RUN_COMMAND:
                return self._execute_run_command(action, working_dir, timeout)
            case _:
                msg = f'Unknown action type: {action.action_type}'
                return SetupActionResult(action=action, success=False, message=msg)

    def _execute_check_plugin(self, action: SetupAction, available_plugins: set[str]) -> SetupActionResult:
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
            self.logger.info(f"Plugin '{action.plugin}' is available")
            # Mark as skipped since the plugin was found - no need to display this
            return SetupActionResult(action=action, success=True, skipped=True)
        else:
            message = f"Required plugin '{action.plugin}' is not available"
            self.logger.error(message)
            return SetupActionResult(action=action, success=False, message=message)

    def _execute_install_package(self, action: SetupAction, environments: dict[str, Environment]) -> SetupActionResult:
        """Executes a package installation.

        Args:
            action: The install action.
            environments: Dict of instantiated environment plugins.

        Returns:
            The result of the installation.
        """
        if action.plugin is None or action.package is None:
            return SetupActionResult(action=action, success=False, message='Plugin or package not specified')

        if action.plugin not in environments:
            return SetupActionResult(action=action, success=False, message=f"Plugin '{action.plugin}' is not available")

        environment = environments[action.plugin]
        self.logger.info(f"Installing '{action.package}' via {action.plugin}")

        try:
            params = InstallParameters(name=action.package, dry=False)
            result = environment.install(params)

            if result is not None:
                return SetupActionResult(action=action, success=True, message=f'Installed {result.name}')
            else:
                return SetupActionResult(action=action, success=False, message=f"Failed to install '{action.package}'")
        except Exception as e:
            return SetupActionResult(action=action, success=False, message=str(e))

    def _execute_run_command(self, action: SetupAction, working_dir: Path, timeout: int) -> SetupActionResult:
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

        self.logger.info(f'Running command: {" ".join(action.command)}')

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
            self.logger.error(message)
            return SetupActionResult(action=action, success=False, message=message)
        except FileNotFoundError:
            message = f'Command not found: {action.command[0]}'
            return SetupActionResult(action=action, success=False, message=message)
        except Exception as e:
            return SetupActionResult(action=action, success=False, message=str(e))
