"""Target parsing and resolution helpers for install-like commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from porringer.backend.command.manifest import manifest_filenames


class TargetKind(StrEnum):
    """Supported target kinds for setup commands."""

    PATH = 'path'
    URL = 'url'
    LINK = 'link'


@dataclass(slots=True)
class TargetResolution:
    """Resolved command target details."""

    kind: TargetKind
    path: Path | None = None
    url: str | None = None
    profile_url: str | None = None
    expected_hash: str | None = None


def find_nearest_manifest(start: Path) -> Path:
    """Find the nearest manifest by walking parent directories.

    Raises:
        ValueError: If no manifest can be found from ``start`` to filesystem root.
    """
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        for filename in manifest_filenames():
            candidate = directory / filename
            if candidate.exists():
                return directory

    raise ValueError(f'No manifest found from {start}')


def parse_link_target(uri: str) -> TargetResolution:
    """Parse ``porringer://`` URI target details.

    Raises:
        ValueError: If the URI is invalid or missing required parameters.
    """
    parsed = urlparse(uri)
    if parsed.scheme != 'porringer':
        raise ValueError(f'Unsupported scheme: {parsed.scheme}')

    route = (parsed.netloc or parsed.path.lstrip('/')).lower()
    if route != 'profile':
        raise ValueError(f'Unsupported link route: {route or "(empty)"}')

    query = parse_qs(parsed.query)
    profile_urls = query.get('url', [])
    if not profile_urls:
        raise ValueError('Install link is missing required "url" parameter')

    expected_hash: str | None = None
    sha_values = query.get('sha256', [])
    if sha_values and sha_values[0]:
        expected_hash = f'sha256:{sha_values[0]}'

    return TargetResolution(
        kind=TargetKind.LINK,
        profile_url=profile_urls[0],
        expected_hash=expected_hash,
    )


def resolve_target(target: str | None) -> TargetResolution:
    """Resolve a command target string to a structured target.

    ``None`` resolves to the nearest manifest from the current directory.
    Bare ``https`` URLs are ambiguous between a setup profile and a manifest;
    callers disambiguate by attempting a strict profile parse first.
    """
    if target is None:
        return TargetResolution(kind=TargetKind.PATH, path=find_nearest_manifest(Path('.')))

    parsed = urlparse(target)

    if parsed.scheme == 'porringer':
        return parse_link_target(target)

    if parsed.scheme in {'http', 'https'}:
        return TargetResolution(kind=TargetKind.URL, url=target)

    return TargetResolution(kind=TargetKind.PATH, path=Path(target))
