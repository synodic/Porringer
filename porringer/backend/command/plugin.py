"""CLI command implementation for plugin.

The plugin command module.

Lists *porringer extension* plugins (e.g. ``porringer-plugin-apt``)
that extend porringer's capabilities.  For operations on packages
*managed by* plugins (e.g. ``requests`` via pip), see :mod:`.package`.
"""

import asyncio
import builtins
import logging

from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.backend.resolver import build_plugin_info
from porringer.core.plugin_schema.plugin_manager import PluginManager
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import PluginInfo

logger = logging.getLogger(__name__)


class PluginCommands:
    """Extension package commands.

    All methods are static — the class acts as a namespace and does
    not require instantiation.  Use `PluginCommands.list()` directly
    or via an `API` instance.
    """

    @staticmethod
    async def list(
        *,
        kinds: builtins.list[PluginKind] | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
        include_managed: bool = False,
    ) -> builtins.list[PluginInfo]:
        """Lists all registered plugins across every plugin group.

        Discovers `environment` (package / tool / runtime),
        `project_environment` (project sync), and `scm` (source control)
        plugins.  Results can be filtered by `kinds`.

        When *plugins* is provided, its environments, project
        environments, and SCM plugins are used directly — no
        entry-point scanning is performed.  ``runtime_context`` is
        extracted from ``plugins.runtime_context`` unless an explicit
        value is supplied.

        When *include_managed* is ``True``, each :class:`PluginManager`
        plugin is queried for its natively installed sub-plugins.
        These appear as additional :class:`PluginInfo` entries whose
        :attr:`~PluginInfo.host_tool` is set to the manager's tool
        name.

        Args:
            kinds: Only include plugins matching these kinds. `None` returns all.
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            runtime_context: Pre-resolved runtime context.  When
                ``None``, a context is resolved automatically from
                available runtime providers.
            include_managed: When ``True``, include sub-plugins reported
                by each :class:`PluginManager` plugin.

        Returns:
            A list of registered plugins, optionally filtered by kind.
        """
        logger.debug('Listing plugins')

        if plugins is not None:
            environments = plugins.environments
            projects = plugins.project_environments
            scm_plugins = plugins.scm_environments
            runtime_context = plugins.resolved_runtime(runtime_context)
        else:
            discovered = discover_all_plugins()
            environments = discovered.environments
            projects = discovered.project_environments
            scm_plugins = discovered.scm_environments

        # Auto-resolve runtime context when the caller did not supply one.
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        all_plugins: dict[str, Plugin] = {**environments, **projects, **scm_plugins}

        results = build_plugin_info(all_plugins, kinds=kinds, runtime_context=runtime_context)

        if include_managed:
            managers: list[tuple[str, PluginManager]] = [
                (name, plugin) for name, plugin in all_plugins.items() if isinstance(plugin, PluginManager)
            ]
            if managers:
                managed_results: list[PluginInfo] = []

                async def _query_manager(name: str, mgr: PluginManager) -> builtins.list[PluginInfo]:
                    tool = mgr.tool_name()
                    sub_pkgs = await mgr.installed_plugins()
                    parent = next(r for r in results if r.name == name)
                    return [
                        PluginInfo(
                            name=pkg.name,
                            kind=parent.kind,
                            version=parent.version,
                            installed=True,
                            tool_version=None,
                            host_tool=tool,
                        )
                        for pkg in sub_pkgs
                    ]

                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(_query_manager(name, mgr)) for name, mgr in managers]
                for task in tasks:
                    managed_results.extend(task.result())
                results.extend(managed_results)

        return results
