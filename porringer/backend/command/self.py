"""Utilities for managing and checking the Porringer installation version."""

import importlib.metadata
import logging

import httpx
from packaging.version import Version

from porringer.schema import PackageUpdateInfo

logger = logging.getLogger(__name__)

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


async def check_for_updates() -> PackageUpdateInfo:
    """Check for updates to the Porringer package by querying PyPI.

    Returns:
        PackageUpdateInfo with current version, latest version, and update status.
    """
    current = get_current_version()
    latest = await get_latest_pypi_version()

    update_available = False
    if current is not None and latest is not None:
        update_available = latest > current

    logger.debug(f'Current version: {current}, Latest version: {latest}')

    return PackageUpdateInfo(
        name=PACKAGE_NAME,
        current_version=current,
        latest_version=latest,
        update_available=update_available,
    )
