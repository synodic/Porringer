"""The setup command module."""

import json
import shlex
import subprocess
import tomllib
from logging import Logger
from pathlib import Path

from porringer.backend.builder import Builder
from porringer.core.plugin_schema.environment import Environment, InstallParameters
from porringer.schema import (
    SetupAction,
    SetupActionResult,
    SetupActionType,
    SetupManifest,
    SetupParameters,
    SetupResults,
)
from porringer.utility.exception import ManifestError
from porringer.utility.utility import canonicalize_type


class SetupCommands:
    """Setup commands for orchestrating project setup."""

    def __init__(self, logger: Logger) -> None:
        """Initialize the SetupCommands class.

        Args:
            logger: Logger instance for logging actions.
        """
        self.logger = logger

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
            return SetupCommands._load_manifest_file(path)

        if path.is_dir():
            # Try porringer.json first, then pyproject.toml
            porringer_file = path / 'porringer.json'
            if porringer_file.exists():
                return SetupCommands._load_manifest_file(porringer_file)

            pyproject_file = path / 'pyproject.toml'
            if pyproject_file.exists():
                return SetupCommands._load_pyproject_manifest(pyproject_file)

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
            return SetupCommands._load_pyproject_manifest(path)

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

    def preview(self, parameters: SetupParameters) -> SetupResults:
        """Previews the setup actions without executing them.

        Args:
            parameters: The setup parameters.

        Returns:
            SetupResults containing the list of actions that would be performed.

        Raises:
            ManifestError: If the manifest cannot be found or parsed.
        """
        self.logger.info(f'Previewing setup from: {parameters.path}')

        manifest_path, manifest = SetupCommands._find_manifest(parameters.path)
        actions = SetupCommands._build_actions(manifest)

        return SetupResults(actions=actions, manifest_path=manifest_path)

    def execute(self, actions: list[SetupAction], parameters: SetupParameters) -> SetupResults:
        """Executes the given setup actions.

        Args:
            actions: The list of actions to execute (from preview).
            parameters: The setup parameters.

        Returns:
            SetupResults containing the results of each action.

        Raises:
            PrerequisiteError: If a required plugin is not available.
            CommandTimeoutError: If a command exceeds the timeout.
        """
        self.logger.info(f'Executing {len(actions)} setup actions')

        # Get fresh plugin state
        environments = self._get_available_environments()
        available_plugins = set(environments.keys())

        results: list[SetupActionResult] = []
        working_dir = parameters.path if parameters.path.is_dir() else parameters.path.parent

        for action in actions:
            result = self._execute_action(action, environments, available_plugins, working_dir, parameters.timeout)
            results.append(result)

            # Fail fast on error
            if not result.success:
                self.logger.error(f'Action failed: {action.description} - {result.message}')
                break

        return SetupResults(actions=actions, results=results)

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
            return SetupActionResult(action=action, success=True)
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
