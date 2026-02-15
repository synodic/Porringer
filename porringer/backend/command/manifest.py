"""Manifest loading, parsing, and validation.

Handles finding, loading, and validating `porringer.json` and
`pyproject.toml` manifests.  Extracted from the monolithic
`sync` module for clarity.
"""

from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Callable
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from porringer.backend.backend import BackendResolver
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import (
    ManifestDiagnostic,
    ManifestDiagnosticSeverity,
    ManifestValidationCode,
    ManifestValidationResult,
    SetupManifest,
)
from porringer.utility.exception import ManifestError, ManifestErrorCode

from .discovery import discover_plugins

logger = logging.getLogger(__name__)

# Maps ManifestErrorCode → ManifestValidationCode for structured classification.
_MANIFEST_ERROR_CODE_MAP: dict[ManifestErrorCode, ManifestValidationCode] = {
    ManifestErrorCode.NO_MANIFEST: ManifestValidationCode.NO_MANIFEST,
    ManifestErrorCode.SYNTAX_ERROR: ManifestValidationCode.SYNTAX_ERROR,
    ManifestErrorCode.LOAD_FAILED: ManifestValidationCode.SCHEMA_INVALID,
    ManifestErrorCode.SCHEMA_INVALID: ManifestValidationCode.SCHEMA_INVALID,
}


def find_manifest(path: Path) -> tuple[Path, SetupManifest]:
    """Finds and loads the setup manifest from the given path.

    Args:
        path: Path to a manifest file or directory containing one.

    Returns:
        Tuple of (manifest_path, parsed_manifest).

    Raises:
        ManifestError: If no valid manifest is found.
    """
    if path.is_file():
        return _load_manifest_file(path)

    if path.is_dir():
        # Try porringer.json first, then pyproject.toml
        porringer_file = path / 'porringer.json'
        if porringer_file.exists():
            return _load_manifest_file(porringer_file)

        pyproject_file = path / 'pyproject.toml'
        if pyproject_file.exists():
            return _load_pyproject_manifest(pyproject_file)

        raise ManifestError(
            f"No manifest found in directory: {path}. Expected 'porringer.json' or 'pyproject.toml'",
            code=ManifestErrorCode.NO_MANIFEST,
        )

    raise ManifestError(f'Path does not exist: {path}', code=ManifestErrorCode.NO_MANIFEST)


def _load_manifest_file(path: Path) -> tuple[Path, SetupManifest]:
    """Loads a manifest from a porringer.json JSON file or pyproject.toml.

    Args:
        path: Path to the manifest file.

    Returns:
        Tuple of (path, parsed_manifest).

    Raises:
        ManifestError: If the file cannot be parsed.
    """
    if path.suffix == '.toml' or path.name == 'pyproject.toml':
        return _load_pyproject_manifest(path)

    # Assume JSON for porringer.json or other files
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return path, SetupManifest.model_validate(data)
    except json.JSONDecodeError as e:
        raise ManifestError(f'Invalid JSON in manifest {path}: {e}', code=ManifestErrorCode.SYNTAX_ERROR) from e
    except Exception as e:
        raise ManifestError(f'Failed to load manifest {path}: {e}', code=ManifestErrorCode.LOAD_FAILED) from e


def _load_pyproject_manifest(path: Path) -> tuple[Path, SetupManifest]:
    """Loads a manifest from pyproject.toml [tool.porringer] section.

    Args:
        path: Path to pyproject.toml.

    Returns:
        Tuple of (path, parsed_manifest).

    Raises:
        ManifestError: If the file cannot be parsed or section is missing.
    """
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)

        tool_section = data.get('tool', {})
        porringer_section = tool_section.get('porringer')

        if porringer_section is None:
            raise ManifestError(
                f'No [tool.porringer] section found in {path}',
                code=ManifestErrorCode.NO_MANIFEST,
            )

        return path, SetupManifest.model_validate(porringer_section)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f'Invalid TOML in {path}: {e}', code=ManifestErrorCode.SYNTAX_ERROR) from e
    except ManifestError:
        raise
    except Exception as e:
        raise ManifestError(
            f'Failed to load pyproject.toml manifest {path}: {e}', code=ManifestErrorCode.LOAD_FAILED
        ) from e


def validate_manifest(path: Path) -> ManifestValidationResult:
    """Validate a manifest for errors without executing any operations.

    Checks syntax, schema version, required fields, plugin availability,
    package-name validity, and duplicate packages across plugins.

    Args:
        path: Path to a manifest file or directory containing one.

    Returns:
        Structured validation result with diagnostics.
    """
    diagnostics: list[ManifestDiagnostic] = []

    def _error(field: str, message: str, code: ManifestValidationCode) -> None:
        diagnostics.append(ManifestDiagnostic(field, message, code, ManifestDiagnosticSeverity.ERROR))

    def _warning(field: str, message: str, code: ManifestValidationCode) -> None:
        diagnostics.append(ManifestDiagnostic(field, message, code, ManifestDiagnosticSeverity.WARNING))

    manifest = _load_manifest_for_validation(path, _error)
    if manifest is None:
        return ManifestValidationResult(diagnostics=diagnostics)

    _validate_schema_version(manifest, _error)

    environments = discover_plugins('environment', Environment, check_dependencies=True)
    project_environments = discover_plugins('project_environment', ProjectEnvironment)
    scm_environments = discover_plugins('scm', ScmEnvironment)
    all_plugins: dict[str, Plugin] = {**environments, **project_environments, **scm_environments}
    resolver = BackendResolver(all_plugins, manifest.preferences)

    _validate_backends(manifest, resolver, _error, _warning)
    _validate_package_names(manifest, resolver, _warning)
    _validate_duplicate_packages(manifest, _warning)
    _validate_injection_support(manifest, resolver, all_plugins, _warning)

    return ManifestValidationResult(diagnostics=diagnostics)


def _load_manifest_for_validation(
    path: Path,
    error_callback: Callable[[str, str, ManifestValidationCode], None],
) -> SetupManifest | None:
    """Load a manifest for validation or record diagnostics."""
    if not path.exists():
        error_callback('path', f'Path does not exist: {path}', ManifestValidationCode.PATH_NOT_FOUND)
        return None

    try:
        _, manifest = find_manifest(path)
    except ManifestError as exc:
        code = _map_manifest_error_code(exc)
        error_callback('', str(exc), code)
        return None

    return manifest


def _map_manifest_error_code(error: ManifestError) -> ManifestValidationCode:
    """Map a manifest loading error to a validation code.

    Uses the structured `ManifestError.code` when available;
    falls back to substring matching for errors without one.
    """
    if error.code is not None:
        return _MANIFEST_ERROR_CODE_MAP.get(error.code, ManifestValidationCode.SCHEMA_INVALID)

    # Legacy fallback: substring matching for errors without a code.
    message = str(error)
    if 'No manifest found' in message or 'No [tool.porringer]' in message:
        return ManifestValidationCode.NO_MANIFEST
    if 'Invalid JSON' in message or 'Invalid TOML' in message:
        return ManifestValidationCode.SYNTAX_ERROR
    return ManifestValidationCode.SCHEMA_INVALID


def _validate_schema_version(
    manifest: SetupManifest,
    error_callback: Callable[[str, str, ManifestValidationCode], None],
) -> None:
    """Validate manifest schema version."""
    supported_versions = {'1'}
    if manifest.version not in supported_versions:
        error_callback(
            'version',
            f"Unsupported schema version '{manifest.version}'. Supported: {', '.join(sorted(supported_versions))}",
            ManifestValidationCode.UNSUPPORTED_VERSION,
        )


def _validate_backends(
    manifest: SetupManifest,
    resolver: BackendResolver,
    error_callback: Callable[[str, str, ManifestValidationCode], None],
    warning_callback: Callable[[str, str, ManifestValidationCode], None] | None = None,
) -> None:
    """Validate that each (kind, ecosystem) in the manifest resolves to a plugin.

    `TOOL` and `RUNTIME` kinds are allowed to have no resolver at
    validation time because the prerequisite tool may be installed by
    an earlier phase during execution.  For those kinds a *warning* is
    emitted (via *warning_callback*) instead of an error.
    """
    # Kinds whose backends may be deferred to a later phase.
    _DEFERRABLE_KINDS = {PluginKind.TOOL, PluginKind.RUNTIME}

    for kind, ecosystem, _packages in manifest.iter_sections():
        installer = resolver.resolve(kind, ecosystem)
        if installer is None:
            if kind in _DEFERRABLE_KINDS and warning_callback is not None:
                warning_callback(
                    f'{kind.value}.{ecosystem}',
                    f"No installer currently available for ({kind.value}, '{ecosystem}'); "
                    'may be resolved after an earlier phase installs the prerequisite',
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )
            else:
                error_callback(
                    f'{kind.value}.{ecosystem}',
                    f"No available installer for ({kind.value}, '{ecosystem}')",
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )


def _validate_injection_support(
    manifest: SetupManifest,
    resolver: BackendResolver,
    all_plugins: dict[str, Plugin],
    warning_callback: Callable[[str, str, ManifestValidationCode], None],
) -> None:
    """Warn when a package declares plugins but its installer does not support injection."""
    for kind, ecosystem, packages in manifest.iter_sections():
        installer = resolver.resolve(kind, ecosystem)
        if installer is None:
            continue
        for j, spec in enumerate(packages):
            if not spec.plugins:
                continue
            plugin = all_plugins.get(installer)
            if plugin is not None and isinstance(plugin, Environment) and not plugin.supports_injection():
                msg = f"Package '{spec.name}' declares plugins but installer '{installer}' does not support injection"
                warning_callback(
                    f'{kind.value}.{ecosystem}[{j}].plugins',
                    msg,
                    ManifestValidationCode.UNKNOWN_PLUGIN,
                )


def _validate_package_names(
    manifest: SetupManifest,
    resolver: BackendResolver,
    warning_callback: Callable[[str, str, ManifestValidationCode], None],
) -> None:
    """Validate package specifiers in a manifest.

    PEP 440 validation is applied when the resolved plugin declares
    `package_name_validator() == 'pep440'`.  Other ecosystems
    accept any non-empty package name.
    """
    for kind, ecosystem, packages in manifest.iter_sections():
        validator = resolver.validator_for(kind, ecosystem)

        for j, spec in enumerate(packages):
            name_str = str(spec.name)
            if not name_str.strip():
                warning_callback(
                    f'{kind.value}.{ecosystem}[{j}].name',
                    'Empty package name',
                    ManifestValidationCode.INVALID_PACKAGE_NAME,
                )
                continue
            if validator == 'pep440':
                try:
                    Requirement(name_str)
                except InvalidRequirement as exc:
                    warning_callback(
                        f'{kind.value}.{ecosystem}[{j}].name',
                        f"Invalid package specifier '{spec.name}': {exc}",
                        ManifestValidationCode.INVALID_PACKAGE_NAME,
                    )


def _validate_duplicate_packages(
    manifest: SetupManifest,
    warning_callback: Callable[[str, str, ManifestValidationCode], None],
) -> None:
    """Warn when packages appear under multiple sections."""
    seen: dict[str, list[str]] = {}
    for kind, ecosystem, packages in manifest.iter_sections():
        label = f'{kind.value}.{ecosystem}'
        for spec in packages:
            canonical = str(canonicalize_name(spec.name.name))
            seen.setdefault(canonical, []).append(label)

    for pkg_name, locations in seen.items():
        if len(locations) > 1:
            warning_callback(
                kind.value,
                f"Package '{pkg_name}' is listed under multiple sections: {', '.join(locations)}",
                ManifestValidationCode.DUPLICATE_PACKAGE,
            )


def manifest_schema() -> dict:
    """Export a JSON Schema representation of the manifest format.

    Returns:
        A dict containing the JSON Schema for `SetupManifest`.
    """
    return SetupManifest.model_json_schema()
