"""Custom URL update source adapter."""

import contextlib
import json
from datetime import datetime
from urllib.request import Request, urlopen

from packaging.version import Version

from porringer.backend.update_sources import UpdateSourceAdapter
from porringer.schema import CheckUpdateParameters, UpdateInfo
from porringer.utility.exception import UpdateError


class CustomURLAdapter(UpdateSourceAdapter):
    """Adapter for checking updates via a custom JSON manifest URL.

    Expected JSON format:
    {
        "version": "1.2.3",
        "download_url": "https://example.com/download/app-1.2.3.exe",
        "release_notes_url": "https://example.com/changelog",
        "published_at": "2026-01-17T10:00:00Z"
    }
    """

    def check(self, parameters: CheckUpdateParameters) -> UpdateInfo:
        """Check for updates from a custom URL.

        Args:
            parameters: Must include 'url' pointing to JSON manifest.

        Returns:
            UpdateInfo with latest release information.

        Raises:
            UpdateError: If the check fails.
        """
        if not parameters.url:
            raise UpdateError("Custom URL source requires 'url' parameter")

        current = Version(parameters.current_version)
        self.logger.info(f'Checking custom URL: {parameters.url}')

        try:
            manifest = self._fetch_manifest(parameters.url)
        except Exception as e:
            raise UpdateError(f'Failed to fetch update manifest: {e}') from e

        # Parse version
        version_str = manifest.get('version')
        if not version_str:
            raise UpdateError("Update manifest missing 'version' field")

        try:
            latest_version = Version(version_str)
        except Exception as e:
            raise UpdateError(f'Invalid version in manifest: {version_str}') from e

        # Skip pre-releases if not requested
        if latest_version.is_prerelease and not parameters.include_prereleases:
            return UpdateInfo(available=False, current_version=current)

        # Parse published date
        published_at = None
        if published_str := manifest.get('published_at'):
            with contextlib.suppress(ValueError):
                published_at = datetime.fromisoformat(published_str.replace('Z', '+00:00'))

        return UpdateInfo(
            available=latest_version > current,
            current_version=current,
            latest_version=latest_version,
            download_url=manifest.get('download_url'),
            release_notes_url=manifest.get('release_notes_url'),
            published_at=published_at,
        )

    @staticmethod
    def _fetch_manifest(url: str) -> dict[str, str]:
        """Fetches update manifest from URL.

        Args:
            url: URL to fetch.

        Returns:
            Parsed JSON manifest.
        """
        request = Request(url)
        request.add_header('Accept', 'application/json')
        request.add_header('User-Agent', 'porringer/1.0')

        with urlopen(request, timeout=30) as response:
            result: dict[str, str] = json.loads(response.read().decode('utf-8'))
            return result
