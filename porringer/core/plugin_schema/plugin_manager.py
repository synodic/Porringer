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

import asyncio
import logging
import re
import shutil
import sys
from abc import abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from porringer.core.plugin_schema.environment import PackageParameters
from porringer.core.schema import Package, PackageRef, PackageRelation, PackageRelationKind
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
    def plugin_add_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Return the CLI command that adds a plugin natively.

        This is used for dry-run / preview display and as the
        default implementation for ``plugin_add``.

        Args:
            plugin: The sub-package to add.
            include_prereleases: When ``True``, allow pre-release
                versions (e.g. append ``--pre`` for pip-based tools).

        Returns:
            A list of command arguments
            (e.g. ``['pdm', 'self', 'add', 'cppython']``).
        """
        ...

    @abstractmethod
    def plugin_update_command(self, plugin: PackageRef, *, include_prereleases: bool = False) -> list[str]:
        """Return the CLI command that upgrades an installed plugin.

        This is used for dry-run / preview display and as the
        default implementation for ``plugin_update``.

        Args:
            plugin: The sub-package to upgrade.
            include_prereleases: When ``True``, allow pre-release
                versions (e.g. append ``--pre`` for pip-based tools).

        Returns:
            A list of command arguments
            (e.g. ``['pdm', 'self', 'add', '--pip-args=--upgrade', 'cppython']``).
        """
        ...

    @abstractmethod
    def plugin_remove_command(self, plugin: PackageRef) -> list[str]:
        """Return the CLI command that removes an installed plugin.

        This is used for dry-run / preview display and as the
        default implementation for ``plugin_remove``.

        Unlike ``plugin_add_command`` and ``plugin_update_command``,
        there is no ``include_prereleases`` parameter because
        pre-release handling is irrelevant when removing a plugin.

        Args:
            plugin: The sub-package to remove.

        Returns:
            A list of command arguments
            (e.g. ``['pdm', 'self', 'remove', 'cppython']``).
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

    async def installed_plugins(self) -> list[Package]:
        """Query the tool for its currently installed plugins.

        Runs ``plugin_list_command`` via ``asyncio.create_subprocess_exec``
        so the event loop is never blocked, and delegates parsing to
        ``parse_plugin_list``.

        Returns:
            A list of installed plugin packages, or an empty list
            on failure.
        """
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_list')
        try:
            args = list(self.plugin_list_command())
            transformed = self._transport.transform_args(args)
            proc = await asyncio.create_subprocess_exec(
                *transformed,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
                _logger.debug('plugin list failed: %s', stderr)
                return []
            relation = PackageRelation(host=tool, kind=PackageRelationKind.PLUGIN)
            return [pkg.model_copy(update={'relation': relation}) for pkg in self.parse_plugin_list(stdout)]
        except FileNotFoundError:
            _logger.debug('%s not found', tool)
            return []
        except Exception as e:
            _logger.debug('Failed to list plugins for %s: %s', tool, e)
            return []

    async def plugin_add(self, params: PackageParameters) -> Package | None:
        """Asynchronously add a plugin via the tool's native command.

        The default implementation delegates to ``plugin_add_command``
        and runs the result as an async subprocess.

        Args:
            params: Package parameters (``params.package`` is the plugin).

        Returns:
            The installed package, or ``None`` on failure.
        """
        args = self.plugin_add_command(params.package, include_prereleases=params.include_prereleases)
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_add')
        try:
            result = await run_command(self._transport.transform_args(args))
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

    async def plugin_update(self, params: PackageParameters) -> Package | None:
        """Asynchronously upgrade an installed plugin via the tool's native command.

        The default implementation delegates to ``plugin_update_command``
        and runs the result as an async subprocess.

        Args:
            params: Package parameters (``params.package`` is the plugin).

        Returns:
            The upgraded package, or ``None`` on failure.
        """
        args = self.plugin_update_command(params.package, include_prereleases=params.include_prereleases)
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_update')
        try:
            result = await run_command(self._transport.transform_args(args))
            _logger.info(result.stdout)
            if result.returncode != 0:
                _logger.error(result.stderr)
                return None
        except FileNotFoundError:
            _logger.error('%s not found', tool)
            return None
        except Exception as e:
            _logger.error('Failed to update plugin %s: %s', params.package.name, e)
            return None
        return Package(name=params.package.name, version=None)

    async def plugin_remove(self, params: PackageParameters) -> Package | None:
        """Asynchronously remove an installed plugin via the tool's native command.

        The default implementation delegates to ``plugin_remove_command``
        and runs the result as an async subprocess.

        Args:
            params: Package parameters (``params.package`` is the plugin).

        Returns:
            The removed package, or ``None`` on failure.
        """
        args = self.plugin_remove_command(params.package)
        tool = self.tool_name()
        _logger = logging.getLogger(f'porringer.{tool}.plugin_remove')
        try:
            result = await run_command(self._transport.transform_args(args))
            _logger.info(result.stdout)
            if result.returncode != 0:
                _logger.error(result.stderr)
                return None
        except FileNotFoundError:
            _logger.error('%s not found', tool)
            return None
        except Exception as e:
            _logger.error('Failed to remove plugin %s: %s', params.package.name, e)
            return None
        return Package(name=params.package.name, version=None)

    def tool_python(self) -> str | None:
        """Return the Python interpreter for this tool's own environment.

        Plugin managers wrap tools (PDM, Poetry, etc.) that maintain
        their own site-packages — separate from both the project
        venv and the installer's Python.  This method returns the
        path to that interpreter so that ``importlib.metadata`` can
        be queried for plugin dependency metadata.

        The default implementation delegates to
        :func:`find_tool_python` which locates the interpreter via
        venv discovery and shebang parsing.  Subclasses may override
        this if the tool exposes its interpreter through a more
        direct mechanism.

        Returns:
            Absolute path to the tool's Python interpreter, or
            ``None`` when it cannot be determined.
        """
        return find_tool_python(self.tool_name())


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


# ---------------------------------------------------------------------------
# Tool interpreter discovery
# ---------------------------------------------------------------------------


def find_tool_python(tool_name: str) -> str | None:
    """Find the Python interpreter inside a CLI tool's own environment.

    Works for tools installed via pipx, uv tool, or pip by inspecting
    the tool's executable:

    1. If the executable lives inside a virtual environment (has a
       ``pyvenv.cfg`` ancestor), the venv's ``python`` is returned.
    2. Otherwise, the executable (or binary launcher) is read and a
       ``#!`` shebang line is extracted to locate the interpreter.

    Returns ``None`` when the interpreter cannot be determined.
    """
    tool_path = shutil.which(tool_name)
    if tool_path is None:
        return None

    tool_exe = Path(tool_path).resolve()

    # Check if the tool itself lives inside a venv
    for parent in tool_exe.parents:
        if (parent / 'pyvenv.cfg').exists():
            python = parent / 'Scripts' / 'python.exe' if sys.platform == 'win32' else parent / 'bin' / 'python'
            if python.is_file():
                return str(python)
            break

    # Parse the executable for an embedded interpreter path (shebang)
    try:
        raw = tool_exe.read_bytes()
    except OSError:
        return None

    text = raw.decode('utf-8', errors='replace')
    match = re.search(r'#!([^\r\n]*[Pp]ython[^\r\n]*)', text)
    if not match:
        return None

    return _resolve_shebang(match.group(1).strip())


def _resolve_shebang(shebang: str) -> str | None:
    """Resolve a shebang string to a Python interpreter path."""
    # Handle "#!/usr/bin/env python3"
    if '/env ' in shebang or '\\env ' in shebang:
        return shutil.which(shebang.rsplit(maxsplit=1)[-1])

    # Direct path — try as-is, then with .exe suffix on Windows
    candidate = Path(shebang)
    if candidate.is_file():
        return str(candidate)
    if sys.platform == 'win32' and not shebang.lower().endswith('.exe'):
        candidate = candidate.with_suffix('.exe')
        if candidate.is_file():
            return str(candidate)

    return None
