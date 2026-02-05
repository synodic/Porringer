"""Utilities for managing and checking the Porringer installation version."""

import importlib.metadata
from logging import Logger

import httpx
from packaging.version import Version

from porringer.schema import PackageUpdateInfo

PYPI_URL = 'https://pypi.org/pypi/porringer/json'
PACKAGE_NAME = 'porringer'


async def get_latest_pypi_version() -> Version | None:
    """Fetch the latest version of porringer from PyPI.

    Returns:
        The latest version as a Version object, or None if fetch failed.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(PYPI_URL)
            response.raise_for_status()
            json_data = response.json()
            version_str = json_data.get('info', {}).get('version')
            if version_str:
                return Version(version_str)
    except httpx.HTTPError, KeyError, ValueError:
        pass
    return None


def get_current_version() -> Version | None:
    """Get the currently installed version of porringer.

    Returns:
        The current version as a Version object, or None if not found.
    """
    try:
        return Version(importlib.metadata.version(PACKAGE_NAME))
    except importlib.metadata.PackageNotFoundError:
        return None


class SelfCommands:
    """Commands related to the Porringer installation."""

    def __init__(self, logger: Logger) -> None:
        """Initialize the SelfCommands class.

        Args:
            logger: Logger instance for logging actions.
        """
        self.logger = logger

    async def check(self) -> PackageUpdateInfo:
        """Check for updates to the Porringer package by querying PyPI.

        Returns:
            PackageUpdateInfo with current version, latest version, and update status.
        """
        current = get_current_version()
        latest = await get_latest_pypi_version()

        update_available = False
        if current is not None and latest is not None:
            update_available = latest > current

        self.logger.debug(f'Current version: {current}, Latest version: {latest}')

        return PackageUpdateInfo(
            name=PACKAGE_NAME,
            current_version=current,
            latest_version=latest,
            update_available=update_available,
        )
