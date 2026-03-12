"""Schema package for Porringer.

All public types are re-exported here for flat access via
``from porringer.schema import X``.
"""

from porringer.schema.cache import DirectoryCache, DirectoryValidationResult, ManifestDirectory
from porringer.schema.check import CheckParameters, CheckResult, PackageUpdateInfo, RuntimeCheckResult
from porringer.schema.config import LocalConfiguration
from porringer.schema.download import DownloadParameters, DownloadResult, HashAlgorithm, ProgressCallback
from porringer.schema.execution import (
    BatchSetupResults,
    CloneStatus,
    CloneStatusKind,
    Install,
    InstallReason,
    Operation,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    Skip,
    SkipReason,
    SyncStrategy,
    Uninstall,
    Upgrade,
)
from porringer.schema.manifest import (
    ManifestDiagnostic,
    ManifestDiagnosticSeverity,
    ManifestMetadata,
    ManifestValidationResult,
    PackageSpec,
    PluginSpec,
    SetupManifest,
)
from porringer.schema.plugin import (
    PluginCapability,
    PluginInfo,
    PluginOperationResult,
    RuntimePackageResult,
    ScopedPackage,
)
from porringer.schema.progress import (
    ActionCompletedEvent,
    ActionStartedEvent,
    CancellationToken,
    DiscoveredPluginEntry,
    ManifestFailedEvent,
    ManifestLoadedEvent,
    ManifestParsedEvent,
    PluginsDiscoveredEvent,
    ProgressEvent,
    SubActionProgress,
    SubActionProgressEvent,
)
from porringer.utility.exception import ManifestValidationCode

__all__ = [
    'ActionCompletedEvent',
    'ActionStartedEvent',
    'BatchSetupResults',
    'CancellationToken',
    'CheckParameters',
    'CheckResult',
    'RuntimeCheckResult',
    'CloneStatus',
    'CloneStatusKind',
    'DirectoryCache',
    'DirectoryValidationResult',
    'DiscoveredPluginEntry',
    'DownloadParameters',
    'DownloadResult',
    'HashAlgorithm',
    'Install',
    'InstallReason',
    'LocalConfiguration',
    'ManifestDiagnostic',
    'ManifestDiagnosticSeverity',
    'ManifestDirectory',
    'ManifestFailedEvent',
    'ManifestLoadedEvent',
    'ManifestMetadata',
    'ManifestParsedEvent',
    'ManifestValidationCode',
    'ManifestValidationResult',
    'Operation',
    'PackageSpec',
    'PackageUpdateInfo',
    'PluginCapability',
    'PluginInfo',
    'PluginOperationResult',
    'PluginsDiscoveredEvent',
    'PluginSpec',
    'RuntimePackageResult',
    'ProgressCallback',
    'ProgressEvent',
    'ScopedPackage',
    'SetupAction',
    'SetupActionResult',
    'SetupManifest',
    'SetupParameters',
    'SetupResults',
    'Skip',
    'SkipReason',
    'SubActionProgress',
    'SubActionProgressEvent',
    'SyncStrategy',
    'Uninstall',
    'Upgrade',
]
