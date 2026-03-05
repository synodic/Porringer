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
from porringer.schema.plugin import PluginInfo, PluginOperationResult, RuntimePackageResult
from porringer.schema.progress import CancellationToken, ProgressEvent, ProgressEventKind, SubActionProgress
from porringer.utility.exception import ManifestValidationCode

__all__ = [
    'BatchSetupResults',
    'CancellationToken',
    'CheckParameters',
    'CheckResult',
    'RuntimeCheckResult',
    'CloneStatus',
    'CloneStatusKind',
    'DirectoryCache',
    'DirectoryValidationResult',
    'DownloadParameters',
    'DownloadResult',
    'HashAlgorithm',
    'Install',
    'InstallReason',
    'LocalConfiguration',
    'ManifestDiagnostic',
    'ManifestDiagnosticSeverity',
    'ManifestDirectory',
    'ManifestMetadata',
    'ManifestValidationCode',
    'ManifestValidationResult',
    'Operation',
    'PackageSpec',
    'PackageUpdateInfo',
    'PluginInfo',
    'PluginOperationResult',
    'PluginSpec',
    'RuntimePackageResult',
    'ProgressCallback',
    'ProgressEvent',
    'ProgressEventKind',
    'SetupAction',
    'SetupActionResult',
    'SetupManifest',
    'SetupParameters',
    'SetupResults',
    'Skip',
    'SkipReason',
    'SubActionProgress',
    'SyncStrategy',
    'Uninstall',
    'Upgrade',
]
