"""Test the command 'self'"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
from packaging.version import Version

from porringer.api import API
from porringer.backend.command.self import (
    PACKAGE_NAME,
    get_current_version,
    get_latest_pypi_version,
)
from porringer.schema import (
    LocalConfiguration,
    PackageUpdateInfo,
)


class TestCommandSelf:
    """Test the command 'self'"""

    @staticmethod
    def test_self_check_returns_package_info() -> None:
        """Test that check() returns PackageUpdateInfo"""
        config = LocalConfiguration()
        api = API(config)

        result = asyncio.run(api.check_self_updates())

        assert isinstance(result, PackageUpdateInfo)
        assert result.name == PACKAGE_NAME


class TestVersionHelpers:
    """Test the version helper functions"""

    @staticmethod
    def test_get_current_version_returns_version() -> None:
        """Test that get_current_version returns a Version object"""
        # porringer should be installed in test environment
        version = get_current_version()
        # Should return a Version or None
        assert version is None or hasattr(version, 'major')

    @staticmethod
    def test_get_latest_pypi_version_success() -> None:
        """Test successful PyPI version fetch"""
        mock_response = Mock()
        mock_response.json.return_value = {'info': {'version': '1.2.3'}}
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        async def _run():
            with patch('porringer.backend.command.self.httpx.AsyncClient', return_value=mock_client):
                return await get_latest_pypi_version()

        version = asyncio.run(_run())
        assert version is not None
        assert str(version) == '1.2.3'

    @staticmethod
    def test_get_latest_pypi_version_network_error() -> None:
        """Test that network errors return None"""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError('Network error')
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        async def _run():
            with patch('porringer.backend.command.self.httpx.AsyncClient', return_value=mock_client):
                return await get_latest_pypi_version()

        version = asyncio.run(_run())
        assert version is None

    @staticmethod
    def test_check_update_available() -> None:
        """Test that check() correctly identifies available updates"""
        config = LocalConfiguration()
        api = API(config)

        async def _run():
            with (
                patch('porringer.backend.command.self.get_current_version', return_value=Version('1.0.0')),
                patch(
                    'porringer.backend.command.self.get_latest_pypi_version',
                    new_callable=AsyncMock,
                    return_value=Version('2.0.0'),
                ),
            ):
                return await api.check_self_updates()

        result = asyncio.run(_run())
        assert result.update_available is True
        assert result.current_version == Version('1.0.0')
        assert result.latest_version == Version('2.0.0')

    @staticmethod
    def test_check_no_update_available() -> None:
        """Test that check() correctly identifies when already up to date"""
        config = LocalConfiguration()
        api = API(config)

        async def _run():
            with (
                patch('porringer.backend.command.self.get_current_version', return_value=Version('2.0.0')),
                patch(
                    'porringer.backend.command.self.get_latest_pypi_version',
                    new_callable=AsyncMock,
                    return_value=Version('2.0.0'),
                ),
            ):
                return await api.check_self_updates()

        result = asyncio.run(_run())
        assert result.update_available is False
