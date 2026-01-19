"""GitHub Releases update source adapter."""

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


class GitHubAdapter(UpdateSourceAdapter):
    """Adapter for checking updates via GitHub Releases API."""

    API_BASE = 'https://api.github.com'

    def check(self, parameters: CheckUpdateParameters) -> UpdateInfo:
        """Check for updates from GitHub Releases.

        Args:
            parameters: Must include 'repo' in "owner/repo" format.

        Returns:
            UpdateInfo with latest release information.

        Raises:
            UpdateError: If the check fails.
        """
        if not parameters.repo:
            raise UpdateError("GitHub source requires 'repo' parameter in 'owner/repo' format")

        current = Version(parameters.current_version)
        self.logger.info(f'Checking GitHub releases for {parameters.repo}')

        # parameters.repo is guaranteed to be non-None by caller check
        releases = self._fetch_releases(parameters.repo or '', parameters.github_token)
        if not releases:
            return UpdateInfo(available=False, current_version=current)

        latest_release = GitHubAdapter._find_latest_release(releases, parameters)
        if not latest_release:
            return UpdateInfo(available=False, current_version=current)

        return self._build_update_info(current, latest_release)

    def _fetch_with_error_handling(self, parameters: CheckUpdateParameters) -> list[dict[str, Any]]:
        """Fetch releases with error handling.

        Args:
            parameters: Check parameters.

        Returns:
            List of releases or empty list on error.

        Raises:
            UpdateError: On API errors.
        """
        try:
            releases = self._fetch_releases(parameters.repo, parameters.github_token) if parameters.repo else None
            if not releases:
                raise UpdateError(f'Invalid repository: {parameters.repo}')
            return releases
        except HTTPError as e:
            match e.code:
                case http.HTTPStatus.FORBIDDEN:
                    raise UpdateError('GitHub API rate limit exceeded. Provide a token with --github-token') from e
                case http.HTTPStatus.NOT_FOUND:
                    raise UpdateError(f'Repository not found: {parameters.repo}') from e
                case _:
                    raise UpdateError(f'GitHub API error: {e}') from e
        except Exception as e:
            raise UpdateError(f'Failed to check GitHub releases: {e}') from e

    @staticmethod
    def _find_latest_release(
        releases: list[dict[str, Any]], parameters: CheckUpdateParameters
    ) -> dict[str, Any] | None:
        """Find latest suitable release from list.

        Args:
            releases: List of releases.
            parameters: Check parameters (for prerelease filter).

        Returns:
            Latest release dict or None.
        """
        for release in releases:
            if release.get('draft', False):
                continue
            if release.get('prerelease', False) and not parameters.include_prereleases:
                continue
            return release

        return None

    def _build_update_info(self, current: Version, release: dict[str, Any]) -> UpdateInfo:
        """Build UpdateInfo from release data.

        Args:
            current: Current version.
            release: Release dict from API.

        Returns:
            UpdateInfo with comparison and download details.
        """
        tag_name = release.get('tag_name', '')
        version_str = tag_name.lstrip('vV')

        with contextlib.suppress(Exception):
            latest_version = Version(version_str)

            published_at = None
            if published_str := release.get('published_at'):
                with contextlib.suppress(ValueError):
                    published_at = datetime.fromisoformat(published_str.replace('Z', '+00:00'))

            download_url = GitHubAdapter._find_download_url(release)

            return UpdateInfo(
                available=latest_version > current,
                current_version=current,
                latest_version=latest_version,
                download_url=download_url,
                release_notes_url=str(release.get('html_url')) if release.get('html_url') else None,
                published_at=published_at,
            )

        self.logger.warning(f'Could not parse version from tag: {tag_name}')
        return UpdateInfo(available=False, current_version=current)

    @staticmethod
    def _find_download_url(release: dict[str, Any]) -> str | None:
        """Find download URL from release data.

        Args:
            release: Release dict from API.

        Returns:
            Download URL or None.
        """
        assets = release.get('assets', [])
        if assets:
            download_url = assets[0].get('browser_download_url')
            if download_url:
                return str(download_url)

        tarball_url = release.get('tarball_url')
        return str(tarball_url) if tarball_url else None

    def _fetch_releases(self, repo: str, token: str | None) -> list[dict[str, Any]]:
        """Fetches releases from GitHub API.

        Args:
            repo: Repository in "owner/repo" format.
            token: Optional GitHub token for authentication.

        Returns:
            List of release objects from API.
        """
        url = f'{self.API_BASE}/repos/{repo}/releases'
        request = Request(url)
        request.add_header('Accept', 'application/vnd.github.v3+json')
        request.add_header('User-Agent', 'porringer/1.0')

        if token:
            request.add_header('Authorization', f'token {token}')

        with urlopen(request, timeout=30) as response:
            result: list[dict[str, Any]] = json.loads(response.read().decode('utf-8'))
            return result
