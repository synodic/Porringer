"""CLI command implementation for client.

Client-oriented aggregate commands.
"""

import asyncio

from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.backend.command.core.inspection import plugin_snapshots
from porringer.backend.command.tool import ToolCommands
from porringer.schema import ClientSnapshot, InspectionMode, ManagedToolReport
from porringer.utility.observability import result_status


class ClientCommands:
    """Aggregate read models for long-lived clients."""

    def __init__(self, tool_commands: ToolCommands) -> None:
        """Initialize client commands."""
        self._tool = tool_commands

    async def snapshot(
        self,
        *,
        inspection_mode: InspectionMode = InspectionMode.FAST,
        include_updates: bool = False,
        plugins: DiscoveredPlugins | None = None,
    ) -> ClientSnapshot:
        """Return a low-latency state bundle for GUI or agent clients."""
        shared_plugins = plugins or await asyncio.to_thread(discover_all_plugins, use_cache=True)
        updates: ManagedToolReport | None = None
        if include_updates:
            updates = await self._tool.check_updates(plugins=shared_plugins)
        diagnostics = updates.diagnostics if updates is not None else ()
        follow_up_actions = updates.follow_up_actions if updates is not None else ()
        return ClientSnapshot(
            status=result_status(updates.success if updates is not None else True, diagnostics),
            inspection_mode=inspection_mode,
            plugins=plugin_snapshots(shared_plugins),
            updates=updates,
            diagnostics=diagnostics,
            follow_up_actions=follow_up_actions,
        )
