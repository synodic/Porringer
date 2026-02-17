"""Schema package for Porringer.

All public types are re-exported here for flat access via
``from porringer.schema import X``.
"""

from porringer.schema.cache import DirectoryCache, DirectoryValidationResult, ManifestDirectory
from porringer.schema.check import CheckParameters, CheckResult, PackageUpdateInfo
from porringer.schema.config import LocalConfiguration
from porringer.schema.download import DownloadParameters, DownloadResult, HashAlgorithm, ProgressCallback
from porringer.schema.execution import (
    BatchSetupResults,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SkipReason,
    SyncStrategy,
)
from porringer.schema.manifest import (
    ManifestDiagnostic,
    ManifestDiagnosticSeverity,
    ManifestMetadata,
    ManifestValidationCode,
    ManifestValidationResult,
    PackageSpec,
    SetupManifest,
)
from porringer.schema.plugin import PluginInfo, PluginOperationResult
from porringer.schema.progress import CancellationToken, ProgressEvent, ProgressEventKind, SubActionProgress

__all__ = [
    'BatchSetupResults',
    'CancellationToken',
    'CheckParameters',
    'CheckResult',
    'DirectoryCache',
    'DirectoryValidationResult',
    'DownloadParameters',
    'DownloadResult',
    'HashAlgorithm',
    'LocalConfiguration',
    'ManifestDiagnostic',
    'ManifestDiagnosticSeverity',
    'ManifestDirectory',
    'ManifestMetadata',
    'ManifestValidationCode',
    'ManifestValidationResult',
    'PackageSpec',
    'PackageUpdateInfo',
    'PluginInfo',
    'PluginOperationResult',
    'ProgressCallback',
    'ProgressEvent',
    'ProgressEventKind',
    'SetupAction',
    'SetupActionResult',
    'SetupManifest',
    'SetupParameters',
    'SetupResults',
    'SkipReason',
    'SubActionProgress',
    'SyncStrategy',
]
