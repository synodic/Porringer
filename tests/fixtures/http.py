"""Shared httpx mock helpers for tests that interact with HTTP APIs.

Provides both a reusable helper function and a pytest fixture for
wiring up ``httpx.AsyncClient`` mocks in async-context-manager form.
"""

from unittest.mock import AsyncMock, MagicMock


def setup_async_client(mock_client: MagicMock, response: MagicMock) -> None:
    """Wire up an ``httpx.AsyncClient`` mock for async-context-manager use.

    After calling this helper the *mock_client* behaves as::

        async with httpx.AsyncClient() as client:
            resp = await client.get(url)  # -> *response*

    Args:
        mock_client: The ``patch('httpx.AsyncClient')`` mock.
        response: The mock response object returned by ``client.get()``.
    """
    instance = MagicMock(get=AsyncMock(return_value=response))
    mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
