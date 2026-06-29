"""Data models and schemas for profile."""

"""Setup profile schemas."""

from typing import Any

from pydantic import Field, model_validator

from porringer.core.schema import PorringerModel
from porringer.schema.execution import BatchSetupResults
from porringer.schema.inspection import SyncInspectionReport
from porringer.schema.observability import SCHEMA_VERSION, Diagnostic, FollowUpAction, ResultStatus


class SetupProfileManifest(PorringerModel):
    """A single remote manifest reference inside a setup profile."""

    url: str
    expected_hash: str | None = Field(
        default=None,
        description='Optional content hash in "algorithm:digest" format used to verify the downloaded manifest',
    )

    @model_validator(mode='before')
    @classmethod
    def _coerce_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {'url': data}
        return data


class SetupProfile(PorringerModel):
    """Portable setup profile referencing one or more manifest URLs."""

    version: str
    name: str
    manifests: list[SetupProfileManifest] = Field(default_factory=list)

    @property
    def manifest_urls(self) -> list[str]:
        """Return manifest URLs as plain strings."""
        return [manifest.url for manifest in self.manifests]


class SetupProfileInspection(PorringerModel):
    """Inspection result for a resolved setup profile."""

    schema_version: str = SCHEMA_VERSION
    operation: str = 'profile.inspect'
    status: ResultStatus = ResultStatus.SUCCESS
    profile: SetupProfile
    inspection: SyncInspectionReport
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)


class SetupProfileExecution(PorringerModel):
    """Execution result for a resolved setup profile."""

    schema_version: str = SCHEMA_VERSION
    operation: str = 'profile.run'
    status: ResultStatus = ResultStatus.SUCCESS
    profile: SetupProfile
    results: BatchSetupResults
    diagnostics: tuple[Diagnostic, ...] = Field(default_factory=tuple)
    follow_up_actions: tuple[FollowUpAction, ...] = Field(default_factory=tuple)
