"""Manifest schemas."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator

from porringer.core.schema import Ecosystem, PackageRef, PlatformScoped, PluginKind
from porringer.utility.exception import ManifestValidationCode  # noqa: F401 - re-exported


class ManifestDiagnosticSeverity(Enum):
    """Severity level for a manifest validation diagnostic."""

    ERROR = auto()
    WARNING = auto()


@dataclass
class ManifestDiagnostic:
    """A single diagnostic produced by manifest validation.

    Args:
        field: Dot-path to the relevant field (e.g. `"packages.python"`, `"preferences.python"`).
        message: Human-readable description of the problem or concern.
        code: Machine-readable diagnostic code.
        severity: Whether this diagnostic is an error or a warning.
    """

    field: str
    message: str
    code: ManifestValidationCode
    severity: ManifestDiagnosticSeverity


@dataclass
class ManifestValidationResult:
    """Structured result of manifest validation.

    Args:
        diagnostics: All validation diagnostics (errors and warnings).
    """

    diagnostics: list[ManifestDiagnostic] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """A manifest is valid when it has no error-level diagnostics."""
        return not any(d.severity == ManifestDiagnosticSeverity.ERROR for d in self.diagnostics)

    @property
    def errors(self) -> list[ManifestDiagnostic]:
        """All error-level diagnostics."""
        return [d for d in self.diagnostics if d.severity == ManifestDiagnosticSeverity.ERROR]

    @property
    def warnings(self) -> list[ManifestDiagnostic]:
        """All warning-level diagnostics."""
        return [d for d in self.diagnostics if d.severity == ManifestDiagnosticSeverity.WARNING]


class PackageSpec(PlatformScoped):
    """A package entry with optional display metadata.

    Supports both string shorthand (just a package specifier) and object form
    with additional metadata for GUI consumers.

    The optional `plugins` list declares sub-packages that should be
    added to the parent package via its native plugin management
    after it is installed.  For example, a PDM installation can
    declare `cppython` as a plugin so that `pdm self add cppython`
    is executed automatically::

        {'name': 'pdm', 'plugins': ['cppython']}

    The field is generic — any tool whose project-environment plugin
    implements ``PluginManager`` can use it.
    """

    name: PackageRef = Field(description='The package reference (name with optional version constraint)')
    description: str | None = Field(default=None, description='Human-readable description of this package')
    plugins: list[PackageRef] = Field(
        default_factory=list,
        description="Sub-packages to add via this tool's native plugin management after installation",
    )

    @model_validator(mode='before')
    @classmethod
    def _coerce_string(cls, data: Any) -> Any:
        """Allow plain strings as shorthand for `{"name": "..."}`."""
        if isinstance(data, str):
            return {'name': data}
        return data


class SetupManifest(BaseModel):
    """The setup manifest schema for .porringer files or pyproject.toml [tool.porringer].

    Manifest entries are grouped by **kind** (`packages`, `tools`,
    `projects`, `runtimes`), each containing a dict keyed by
    **ecosystem** (e.g. `"python"`, `"node"`, `"system"`).

    Ecosystem names are free-form strings declared by plugins — the core
    schema does not enumerate them.  A third-party Cargo plugin declaring
    `ecosystem() = "rust"` "just works" with
    `"packages": {"rust": ["serde"]}` — zero core changes required.
    """

    version: str = Field(default='1', description='Manifest schema version')
    name: str | None = Field(default=None, description='Human-readable project/environment name')
    description: str | None = Field(default=None, description='Short description shown in the install preview header')
    author: str | None = Field(default=None, description='Author or organization name')
    url: HttpUrl | None = Field(default=None, description='Project URL for reference')
    packages: dict[Ecosystem, list[PackageSpec]] = Field(
        default_factory=dict, description='Packages to install per ecosystem (e.g. {"python": ["requests"]})'
    )
    tools: dict[Ecosystem, list[PackageSpec]] = Field(
        default_factory=dict, description='CLI tools to install per ecosystem (e.g. {"python": ["pdm"]})'
    )
    projects: dict[Ecosystem, list[PackageSpec]] = Field(
        default_factory=dict, description='Project sync targets per ecosystem (e.g. {"python": []})'
    )
    runtimes: dict[Ecosystem, list[PackageSpec]] = Field(
        default_factory=dict, description='Language runtimes to install per ecosystem (e.g. {"python": ["3.12"]})'
    )
    scm: dict[Ecosystem, list[PackageSpec]] = Field(
        default_factory=dict,
        description='SCM repositories to clone per ecosystem (e.g. {"git": ["https://github.com/org/repo"]})',
    )
    preferences: dict[Ecosystem, str] = Field(
        default_factory=dict,
        description='Preferred installer per ecosystem (e.g. {"python": "uv"})',
    )
    extends: list[str] = Field(
        default_factory=list,
        description='Paths to other manifests whose state is merged (base layers)',
    )
    post_sync: list[str] = Field(default_factory=list, description='Commands to run after state synchronisation')

    def iter_sections(self) -> Iterator[tuple[PluginKind, Ecosystem, list[PackageSpec]]]:
        """Yield `(kind, ecosystem, packages)` for every non-empty section.

        Replaces the repeated `for kind in PluginKind: getattr(…)`
        pattern used throughout the sync engine.
        """
        for kind in PluginKind:
            section: dict[Ecosystem, list[PackageSpec]] = getattr(self, kind.value, {})
            for ecosystem, packages in section.items():
                yield kind, ecosystem, packages


@dataclass
class ManifestResult:
    """Result of locating and loading a manifest.

    Separates the physical manifest file from the logical project root.
    For a native ``porringer.json`` the root is the file's parent.
    For a reference from ``pyproject.toml`` (via ``manifest = "path"``) the
    root is the referencing file's parent while ``manifest_path`` points to
    the resolved target.

    Args:
        manifest_path: Absolute path to the file that was actually parsed
            as a ``SetupManifest``.
        root_directory: Logical project root — the directory that downstream
            phases use as the working directory.
        manifest: The parsed manifest data.
    """

    manifest_path: Path
    root_directory: Path
    manifest: SetupManifest


@dataclass
class ManifestMetadata:
    """Display metadata from a setup manifest.

    Carries optional human-readable information for GUI consumers
    (e.g. install preview screens).

    Args:
        name: Human-readable project/environment name.
        description: Short description shown in the install preview header.
        author: Author or organization name.
        url: Project URL for reference.
    """

    name: str | None = None
    description: str | None = None
    author: str | None = None
    url: str | None = None
