"""Data models and schemas for snapshot.

Client snapshot schemas for long-lived downstream applications.
"""

from pydantic import Field

from porringer.core.schema import PorringerModel
from porringer.schema.inspection import DiscoveredPluginSnapshot, InspectionMode
from porringer.schema.observability import SCHEMA_VERSION, Diagnostic, FollowUpAction, ResultStatus
from porringer.schema.tool import ManagedToolReport


class ClientSnapshot(PorringerModel):
    """Low-latency state bundle for GUI and agent clients."""

    schema_version: str = SCHEMA_VERSION
    operation: str = 'client.snapshot'
    status: ResultStatus = ResultStatus.SUCCESS
    inspection_mode: InspectionMode = InspectionMode.FAST
    plugins: tuple[DiscoveredPluginSnapshot, ...] = Field(default_factory=tuple)
    updates: ManagedToolReport | None = None
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)
