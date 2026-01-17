"""PyPI update source adapter."""

import contextlib
import http
import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from packaging.version import Version

from porringer.backend.update_sources import UpdateSourceAdapter
from porringer.schema import CheckUpdateParameters, UpdateInfo
from porringer.utility.exception import UpdateError


class PyPIAdapter(UpdateSourceAdapter):
    """Adapter for checking updates via PyPI JSON API."""

    API_BASE = 'https://pypi.org/pypi'

    def check(self, parameters: CheckUpdateParameters) -> UpdateInfo:
        """Check for updates from PyPI.

        Args:
            parameters: Must include 'package' with PyPI package name.

        Returns:
            UpdateInfo with latest release information.

        Raises:
            UpdateError: If the check fails.
        """
        if not parameters.package:
            raise UpdateError("PyPI source requires 'package' parameter")

        current = Version(parameters.current_version)
        self.logger.info(f'Checking PyPI for {parameters.package}')

        package_info = self._fetch_with_error_handling(parameters)
        if not package_info:
            return UpdateInfo(available=False, current_version=current)

        latest_version = self._get_latest_version(package_info, parameters)
        if not latest_version or latest_version == current:
            return UpdateInfo(available=False, current_version=current)

        return self._build_update_info(current, latest_version, parameters.package, package_info)

    def _fetch_with_error_handling(self, parameters: CheckUpdateParameters) -> dict[str, Any] | None:
        """Fetch package info with error handling.

        Args:
            parameters: Check parameters.

        Returns:
            Package info dict or None on error.

        Raises:
            UpdateError: On API errors.
        """
        try:
            package_info = self._fetch_package_info(parameters.package) if parameters.package else None
            if not package_info:
                raise UpdateError(f'Invalid package: {parameters.package}')
            return package_info
        except HTTPError as e:
            match e.code:
                case http.HTTPStatus.NOT_FOUND:
                    raise UpdateError(f'Package not found on PyPI: {parameters.package}') from e
                case _:
                    raise UpdateError(f'PyPI API error: {e}') from e
        except Exception as e:
            raise UpdateError(f'Failed to check PyPI: {e}') from e

    def _get_latest_version(self, package_info: dict[str, Any], parameters: CheckUpdateParameters) -> Version | None:
        """Extract latest version from package info.

        Args:
            package_info: Package info from API.
            parameters: Check parameters (for prerelease filter).

        Returns:
            Latest suitable version or None.
        """
        info = package_info.get('info', {})
        latest_version_str = info.get('version')

        if not latest_version_str:
            return None

        with contextlib.suppress(Exception):
            latest_version = Version(latest_version_str)

            if latest_version.is_prerelease and not parameters.include_prereleases:
                return self._find_stable_version(package_info)

            return latest_version

        self.logger.warning(f'Could not parse version: {latest_version_str}')
        return None

    @staticmethod
    def _find_stable_version(package_info: dict[str, Any]) -> Version | None:
        """Find latest stable (non-prerelease) version.

        Args:
            package_info: Package info from API.

        Returns:
            Latest stable version or None.
        """
        releases = package_info.get('releases', {})
        stable_versions = []

        for ver_str in releases:
            with contextlib.suppress(Exception):
                ver = Version(ver_str)
                if not ver.is_prerelease:
                    stable_versions.append(ver)

        return max(stable_versions) if stable_versions else None

    def _build_update_info(
        self,
        current: Version,
        latest_version: Version,
        package: str,
        package_info: dict[str, Any],
    ) -> UpdateInfo:
        """Build UpdateInfo from package data.

        Args:
            current: Current version.
            latest_version: Latest version.
            package: Package name.
            package_info: Package info from API.

        Returns:
            UpdateInfo with comparison and download details.
        """
        releases = package_info.get('releases', {})
        published_at = self._get_published_time(releases, latest_version)

        package_url = package_info.get('info', {}).get('package_url') or f'https://pypi.org/project/{package}/'
        download_url: str | None = self._find_download_url(
            package_info.get('info', {}).get('download_url'), releases, latest_version
        )

        return UpdateInfo(
            available=latest_version > current,
            current_version=current,
            latest_version=latest_version,
            download_url=download_url,
            release_notes_url=package_url,
            published_at=published_at,
        )

    @staticmethod
    def _get_published_time(releases: dict[str, Any], latest_version: Version) -> datetime | None:
        """Extract published time from release files.

        Args:
            releases: Releases dict from API.
            latest_version: Version to find.

        Returns:
            Published datetime or None.
        """
        release_files = releases.get(str(latest_version), [])

        if not release_files:
            return None

        upload_time_str = release_files[0].get('upload_time_iso_8601')

        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(upload_time_str.replace('Z', '+00:00'))

        return None

    @staticmethod
    def _find_download_url(direct_url: str | None, releases: dict[str, Any], latest_version: Version) -> str | None:
        """Find download URL from multiple sources.

        Args:
            direct_url: Direct download URL from info.
            releases: Releases dict from API.
            latest_version: Version to find.

        Returns:
            Download URL or None.
        """
        if direct_url:
            return direct_url

        release_files = releases.get(str(latest_version), [])

        for file_info in release_files:
            if file_info.get('packagetype') in {'bdist_wheel', 'sdist'}:
                url = file_info.get('url')
                return str(url) if url else None

        return None

    def _fetch_package_info(self, package: str) -> dict[str, Any]:
        """Fetches package info from PyPI JSON API.

        Args:
            package: Package name.

        Returns:
            Package info dict from API.
        """
        url = f'{self.API_BASE}/{package}/json'
        request = Request(url)
        request.add_header('Accept', 'application/json')
        request.add_header('User-Agent', 'porringer/1.0')

        with urlopen(request, timeout=30) as response:
            result: dict[str, Any] = json.loads(response.read().decode('utf-8'))
            return result
