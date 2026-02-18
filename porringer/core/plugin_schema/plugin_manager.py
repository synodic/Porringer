"""Protocol for tools that manage their own plugins natively.

Tools like PDM and Poetry support native plugin management via their
own CLI commands (``pdm self add``, ``poetry self add``).  Plugins
that wrap such tools implement ``PluginManager`` so the sync engine
routes plugin-management actions to the tool's own command.

The protocol follows the same mixin pattern used by
``RuntimeProvider`` / ``RuntimeConsumer`` — the sync engine checks
``isinstance(plugin, PluginManager)`` to decide whether native
plugin management is available.
"""

from __future__ import annotations

import logging
import subprocess
from abc import abstractmethod
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.schema import Package, PackageRef
from porringer.utility.utility import run_command


@runtime_checkable
class PluginManager(Protocol):
    """A plugin that can manage its own sub-plugins via native CLI commands.

    Only plugins that wrap tools with built-in plugin management need
    to implement this.  The sync engine checks
    ``isinstance(plugin, PluginManager)`` when processing plugin-
    management actions and routes to the matching ``PluginManager``.

    Implementers must also provide ``tool_name()`` (inherited from
    ``ToolBasedPlugin``) which is used to match the ``plugin_target``
    on a ``SetupAction``.
    """

    @classmethod
    @abstractmethod
    def tool_name(cls) -> str:
        """Return the CLI executable name this plugin wraps.

        Used to match ``action.plugin_target.name`` against this
        plugin manager.
        """
        ...

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Check if the underlying tool is available on the system.

        Implementers inherit this from ``ToolBasedPlugin`` which checks
        ``shutil.which(tool_name())``.
        """
        ...

    @abstractmethod
    def plugin_add_command(self, plugin: PackageRef) -> list[str]:
        """Return the CLI command that adds a plugin natively.

        This is used for dry-run / preview display and as the
        default implementation for ``async_plugin_add``.

        Args:
            plugin: The sub-package to add.

        Returns:
            A list of command arguments
            (e.g. ``['pdm', 'self', 'add', 'cppython']``).
        """
        ...

    @abstractmethod
    def plugin_list_command(self) -> list[str]:
        """Return the CLI command that lists installed plugins.

        The command should produce output that ``parse_plugin_list``
        can interpret.

        Returns:
            A list of command arguments
            (e.g. ``['pdm', 'self', 'list']``).
        """
        ...

    @staticmethod
    def parse_plugin_list(stdout: str) -> list[Package]:
        """Parse the output of ``plugin_list_command`` into packages.

        The default implementation treats each non-empty line as a
        package name (no version).  Subclasses should override this
        to match their tool's output format.

        Args:
            stdout: The captured standard output of the list command.

        Returns:
            A list of installed plugin packages.
        """
        return [Package(name=line.strip(), version=None) for line in stdout.splitlines() if line.strip()]

    def installed_plugins(self) -> list[Package]:
        """Query the tool for its currently installed plugins.

        Runs ``plugin_list_command`` synchronously and delegates
        parsing to ``parse_plugin_list``.

        Returns:
            A list of installed plugin packages, or an empty list
            on failure.
        """
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_list')
        try:
            result = subprocess.run(
                self.plugin_list_command(),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                _logger.debug('plugin list failed: %s', result.stderr)
                return []
            return self.parse_plugin_list(result.stdout)
        except FileNotFoundError:
            _logger.debug('%s not found', tool)
            return []
        except Exception as e:
            _logger.debug('Failed to list plugins for %s: %s', tool, e)
            return []

    async def async_plugin_add(self, params: PackageParameters) -> Package | None:
        """Asynchronously add a plugin via the tool's native command.

        The default implementation delegates to ``plugin_add_command``
        and runs the result as an async subprocess.

        Args:
            params: Package parameters (``params.package`` is the plugin).

        Returns:
            The installed package, or ``None`` on failure.
        """
        args = self.plugin_add_command(params.package)
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_add')
        try:
            result = await run_command(args)
            _logger.info(result.stdout)
            if result.returncode != 0:
                _logger.error(result.stderr)
                return None
        except FileNotFoundError:
            _logger.error('%s not found', tool)
            return None
        except Exception as e:
            _logger.error('Failed to add plugin %s: %s', params.package.name, e)
            return None
        return Package(name=params.package.name, version=None)


def find_plugin_manager(
    tool_name: str,
    project_environments: Mapping[str, object] | None,
) -> PluginManager | None:
    """Find a ``PluginManager`` for the given tool name.

    Iterates *project_environments* looking for one that implements
    ``PluginManager``, has a matching ``tool_name()``, and is
    available on PATH.

    This helper is used by both the execution engine (to route
    plugin-management actions) and the action builder (to generate
    CLI preview commands).

    Args:
        tool_name: The CLI tool name to match (e.g. ``"pdm"``).
        project_environments: Dict of project-environment plugin
            instances.  Values are checked via ``isinstance``.

    Returns:
        The matching ``PluginManager``, or ``None`` if none found.
    """
    if not project_environments:
        return None
    for proj_env in project_environments.values():
        if isinstance(proj_env, PluginManager) and proj_env.tool_name() == tool_name and proj_env.is_available():
            return proj_env
    return None
