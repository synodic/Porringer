"""The update command module."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from porringer.backend.builder import Builder
from porringer.backend.cache import DirectoryCacheManager
from porringer.core.plugin_schema.environment import Environment, InstallParameters
from porringer.core.schema import Package, PackageName
from porringer.schema import (
    BatchSetupResults,
    CancellationToken,
    DownloadParameters,
    DownloadResult,
    InstallProgressCallback,
    ProgressCallback,
    SetupAction,
    SetupActionResult,
    SetupActionType,
    SetupManifest,
    SetupParameters,
    SetupResults,
)
from porringer.utility.download import download_file
from porringer.utility.exception import ManifestError, PluginError
from porringer.utility.utility import canonicalize_type


@dataclass
class _InstallContext:
    """Context for install operations."""

    parameters: SetupParameters
    progress_callback: InstallProgressCallback | None
    cancellation_token: CancellationToken | None = None


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
            if not prereq.is_applicable():
                continue
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

    @staticmethod
    def _is_package_installed(
        package: str,
        installed_packages: list[Package],
    ) -> tuple[bool, str | None]:
        """Checks if a package is already installed with a compatible version.

        Args:
            package: The package specification (e.g., 'ruff>=0.1.0')
            installed_packages: List of installed packages from the environment

        Returns:
            Tuple of (is_installed, skip_reason or None)
        """
        try:
            req = Requirement(package)
        except InvalidRequirement:
            # If we can't parse, just do name matching with canonicalization
            canonical_name = canonicalize_name(package.strip())
            for installed in installed_packages:
                if canonicalize_name(str(installed.name)) == canonical_name:
                    return True, f'{installed.name}=={installed.version} already installed'
            return False, None

        canonical_name = canonicalize_name(req.name)
        for installed in installed_packages:
            if canonicalize_name(str(installed.name)) == canonical_name:
                if not req.specifier:
                    return True, f'{installed.name}=={installed.version} already installed'
                if installed.version is not None:
                    try:
                        if Version(installed.version) in req.specifier:
                            return True, f'{installed.name}=={installed.version} satisfies {package}'
                    except InvalidVersion:
                        pass
        return False, None

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

        # Check if package is already installed
        try:
            installed_packages = environment.packages()
            is_installed, skip_reason = UpdateCommands._is_package_installed(action.package, installed_packages)
            if is_installed:
                self.logger.info(f"Skipping '{action.package}': {skip_reason}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=skip_reason,
                )
        except PluginError as e:
            self.logger.debug(f'Plugin error checking packages for {action.plugin}: {e}')
        except Exception as e:
            self.logger.debug(f'Could not check installed packages for {action.plugin}: {e}')

        self.logger.info(f"Installing '{action.package}' via {action.plugin}")
        return self._attempt_package_installation(action, environment)

    def _attempt_package_installation(self, action: SetupAction, environment: Environment) -> SetupActionResult:
        """Attempt to install a package via the given environment plugin.

        Args:
            action: The install action.
            environment: The environment plugin to use for installation.

        Returns:
            The result of the installation attempt.
        """
        success = False
        message = ''

        try:
            params = InstallParameters(name=action.package, dry=False)
            result = environment.install(params)
            if result is not None:
                success = True
                message = f'Installed {result.name}'
            else:
                message = f"Failed to install '{action.package}'"
        except PluginError as e:
            self.logger.error(f'Plugin error installing {action.package}: {e}')
            message = str(e)
        except subprocess.SubprocessError as e:
            self.logger.error(f'Subprocess error installing {action.package}: {e}')
            message = str(e)
        except Exception as e:
            message = str(e)

        return SetupActionResult(action=action, success=success, message=message)

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

    # --- Async Execution Methods ---

    async def _async_execute_install_package(
        self,
        action: SetupAction,
        environments: dict[str, Environment],
    ) -> SetupActionResult:
        """Asynchronously executes a package installation.

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

        # Check if package is already installed
        try:
            # Run packages() in executor since it may be blocking
            loop = asyncio.get_running_loop()
            installed_packages = await loop.run_in_executor(None, environment.packages)
            is_installed, skip_reason = UpdateCommands._is_package_installed(action.package, installed_packages)
            if is_installed:
                self.logger.info(f"Skipping '{action.package}': {skip_reason}")
                return SetupActionResult(
                    action=action,
                    success=True,
                    skipped=True,
                    skip_reason=skip_reason,
                )
        except PluginError as e:
            self.logger.debug(f'Plugin error checking packages for {action.plugin}: {e}')
        except Exception as e:
            self.logger.debug(f'Could not check installed packages for {action.plugin}: {e}')

        self.logger.info(f"Installing '{action.package}' via {action.plugin}")
        return await self._attempt_async_package_installation(action, environment)

    async def _attempt_async_package_installation(
        self, action: SetupAction, environment: Environment
    ) -> SetupActionResult:
        """Attempt to asynchronously install a package via the given environment plugin.

        Args:
            action: The install action.
            environment: The environment plugin to use for installation.

        Returns:
            The result of the installation attempt.
        """
        success = False
        message = ''

        try:
            params = InstallParameters(name=action.package, dry=False)
            result = await environment.async_install(params)

            if result is not None:
                success = True
                message = f'Installed {result.name}'
            else:
                message = f"Failed to install '{action.package}'"
        except PluginError as e:
            self.logger.error(f'Plugin error installing {action.package}: {e}')
            message = str(e)
        except asyncio.CancelledError:
            self.logger.error(f'Installation cancelled for {action.package}')
            message = 'Installation cancelled'
        except TimeoutError as e:
            self.logger.error(f'Timeout installing {action.package}: {e}')
            message = str(e)
        except Exception as e:
            message = str(e)

        return SetupActionResult(action=action, success=success, message=message)

    async def _execute_check_actions_async(
        self,
        check_actions: list[SetupAction],
        available_plugins: set[str],
        parameters: SetupParameters,
        progress_callback: InstallProgressCallback | None,
    ) -> tuple[list[SetupActionResult], bool]:
        """Execute CHECK_PLUGIN actions sequentially.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        results: list[SetupActionResult] = []
        for action in check_actions:
            if parameters.dry_run:
                result = self._dry_run_action(action, available_plugins)
            else:
                result = self._execute_check_plugin(action, available_plugins)
            results.append(result)
            if progress_callback:
                progress_callback(action, result)
            if not result.success and not result.skipped:
                self.logger.error(f'Action failed: {action.description} - {result.message}')
                if parameters.fail_fast:
                    return results, False
        return results, True

    async def _execute_install_actions_async(
        self,
        install_actions: list[SetupAction],
        environments: dict[str, Environment],
        available_plugins: set[str],
        context: _InstallContext,
    ) -> tuple[list[SetupActionResult], bool]:
        """Execute INSTALL_PACKAGE actions with parallel support.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        if context.parameters.dry_run:
            return self._dry_run_install_actions(install_actions, available_plugins, context.progress_callback), True

        parallel_actions, sequential_actions = self._group_actions_by_parallelism(install_actions, environments)

        results: list[SetupActionResult] = []

        # Execute parallel actions concurrently
        if parallel_actions:
            parallel_results, should_continue = await self._run_parallel_installs(
                parallel_actions, environments, context
            )
            results.extend(parallel_results)
            if not should_continue:
                return results, False

        # Execute sequential actions one at a time
        sequential_results, should_continue = await self._run_sequential_installs(
            sequential_actions, environments, context
        )
        results.extend(sequential_results)

        return results, should_continue

    def _dry_run_install_actions(
        self,
        install_actions: list[SetupAction],
        available_plugins: set[str],
        progress_callback: InstallProgressCallback | None,
    ) -> list[SetupActionResult]:
        """Execute dry-run for install actions."""
        results: list[SetupActionResult] = []
        for action in install_actions:
            result = self._dry_run_action(action, available_plugins)
            results.append(result)
            if progress_callback:
                progress_callback(action, result)
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

    async def _run_sequential_installs(
        self,
        sequential_actions: list[SetupAction],
        environments: dict[str, Environment],
        context: _InstallContext,
    ) -> tuple[list[SetupActionResult], bool]:
        """Run install actions sequentially with cancellation support."""
        results: list[SetupActionResult] = []
        for action in sequential_actions:
            # Check for cancellation before each install
            if context.cancellation_token is not None:
                context.cancellation_token.raise_if_cancelled()

            if context.progress_callback:
                context.progress_callback(action, None)
            result = await self._async_execute_install_package(action, environments)
            results.append(result)
            if context.progress_callback:
                context.progress_callback(action, result)
            if not result.success and not result.skipped and context.parameters.fail_fast:
                self.logger.error(f'Action failed: {action.description} - {result.message}')
                return results, False
        return results, True

    async def _run_parallel_installs(
        self,
        parallel_actions: list[SetupAction],
        environments: dict[str, Environment],
        context: _InstallContext,
    ) -> tuple[list[SetupActionResult], bool]:
        """Run install actions in parallel using TaskGroup.

        Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.
        All tasks are automatically cancelled if any raises an unhandled exception.

        Returns:
            Tuple of (results, should_continue). should_continue is False if fail_fast triggered.
        """
        # Check for cancellation before starting parallel installs
        if context.cancellation_token is not None:
            context.cancellation_token.raise_if_cancelled()

        results: dict[int, SetupActionResult] = {}
        action_indices = {id(action): i for i, action in enumerate(parallel_actions)}

        async def install_with_callback(action: SetupAction) -> None:
            if context.progress_callback:
                context.progress_callback(action, None)
            try:
                result = await self._async_execute_install_package(action, environments)
            except Exception as e:
                result = SetupActionResult(action=action, success=False, message=str(e))
            if context.progress_callback:
                context.progress_callback(action, result)
            results[action_indices[id(action)]] = result

        try:
            async with asyncio.TaskGroup() as tg:
                for action in parallel_actions:
                    tg.create_task(install_with_callback(action))
        except ExceptionGroup as eg:
            # TaskGroup raises ExceptionGroup if any task fails with unhandled exception
            # Our install_with_callback catches exceptions, so this shouldn't happen normally
            self.logger.error(f'Parallel install failed with exceptions: {eg.exceptions}')

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
            if not action_result.success and not action_result.skipped and context.parameters.fail_fast:
                self.logger.error(f'Action failed: {action.description} - {action_result.message}')
                return final_results, False

        return final_results, True

    async def _execute_command_actions_async(
        self,
        command_actions: list[SetupAction],
        available_plugins: set[str],
        working_dir: Path,
        parameters: SetupParameters,
        progress_callback: InstallProgressCallback | None,
    ) -> list[SetupActionResult]:
        """Execute RUN_COMMAND actions sequentially."""
        results: list[SetupActionResult] = []
        for action in command_actions:
            if parameters.dry_run:
                result = self._dry_run_action(action, available_plugins)
            else:
                result = self._execute_run_command(action, working_dir, parameters.timeout)
            results.append(result)
            if progress_callback:
                progress_callback(action, result)
            if not result.success and not result.skipped:
                self.logger.error(f'Action failed: {action.description} - {result.message}')
                if parameters.fail_fast:
                    break
        return results

    async def execute_single_async(
        self,
        actions: list[SetupAction],
        path: Path,
        parameters: SetupParameters,
        progress_callback: InstallProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> SetupResults:
        """Asynchronously executes setup actions for a single path with parallel support.

        Package installations are executed in parallel when plugins support it.
        CHECK_PLUGIN and RUN_COMMAND actions are executed sequentially.

        Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency.

        Args:
            actions: The list of actions to execute (from preview).
            path: The path this execution is for (used for working directory).
            parameters: The setup parameters.
            progress_callback: Optional callback for progress updates.
            cancellation_token: Optional token for cooperative cancellation.

        Returns:
            SetupResults containing the results of each action.

        Raises:
            asyncio.CancelledError: If cancellation_token is cancelled.
        """
        # Check for cancellation before starting
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()

        self.logger.info(f'Executing {len(actions)} setup actions async (dry_run={parameters.dry_run})')

        environments = self._get_available_environments()
        available_plugins = set(environments.keys())
        working_dir = path if path.is_dir() else path.parent

        # Populate CLI commands for all actions
        for action in actions:
            action.cli_command = UpdateCommands._get_cli_command(action, environments)

        # Separate actions by type
        check_actions = [a for a in actions if a.action_type == SetupActionType.CHECK_PLUGIN]
        install_actions = [a for a in actions if a.action_type == SetupActionType.INSTALL_PACKAGE]
        command_actions = [a for a in actions if a.action_type == SetupActionType.RUN_COMMAND]

        results: list[SetupActionResult] = []

        # Execute CHECK_PLUGIN actions
        check_results, should_continue = await self._execute_check_actions_async(
            check_actions, available_plugins, parameters, progress_callback
        )
        results.extend(check_results)
        if not should_continue:
            return SetupResults(actions=actions, results=results)

        # Execute INSTALL_PACKAGE actions
        if install_actions:
            context = _InstallContext(
                parameters=parameters,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
            )
            install_results, should_continue = await self._execute_install_actions_async(
                install_actions,
                environments,
                available_plugins,
                context,
            )
            results.extend(install_results)
            if not should_continue:
                return SetupResults(actions=actions, results=results)

        # Execute RUN_COMMAND actions
        command_results = await self._execute_command_actions_async(
            command_actions, available_plugins, working_dir, parameters, progress_callback
        )
        results.extend(command_results)

        return SetupResults(actions=actions, results=results)

    async def execute_batch_async(
        self,
        previews: BatchSetupResults,
        parameters: SetupParameters,
        progress_callback: InstallProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> BatchSetupResults:
        """Asynchronously execute setup actions for multiple manifests.

        Uses structured concurrency patterns for clean cancellation.

        Args:
            previews: The batch preview results containing actions per manifest.
            parameters: The setup parameters.
            progress_callback: Optional callback for progress updates.
            cancellation_token: Optional token for cooperative cancellation.

        Returns:
            BatchSetupResults containing execution results for each manifest.

        Raises:
            asyncio.CancelledError: If cancellation_token is cancelled.
        """
        self.logger.info(f'Executing setup async for {len(previews.manifest_results)} manifest(s)')

        manifest_results: list[SetupResults] = []
        failed_paths: list[tuple[Path, str]] = list(previews.failed_paths)

        for preview in previews.manifest_results:
            # Check for cancellation before each manifest
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()

            if preview.manifest_path is None:
                continue

            result = await self.execute_single_async(
                preview.actions,
                preview.manifest_path,
                parameters,
                progress_callback,
                cancellation_token,
            )
            result.manifest_path = preview.manifest_path
            manifest_results.append(result)

            # Check for failures
            has_failure = any(not r.success for r in result.results)
            if has_failure and parameters.fail_fast:
                break

        return BatchSetupResults(manifest_results=manifest_results, failed_paths=failed_paths)
