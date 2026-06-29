"""Helpers for http."""

"""Shared aiohttp mock helpers for tests that interact with HTTP APIs.

Provides both a reusable helper function and a pytest fixture for
wiring up ``aiohttp.ClientSession`` mocks in async-context-manager form.
"""

from unittest.mock import AsyncMock, MagicMock


def setup_async_client(mock_session_cls: MagicMock, response: MagicMock) -> None:
    """Wire up an ``aiohttp.ClientSession`` mock for async-context-manager use.

    After calling this helper the *mock_session_cls* behaves as::

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:  # -> *response*

    Args:
        mock_session_cls: The ``patch('aiohttp.ClientSession')`` mock.
        response: The mock response object returned by ``session.get()``.
    """
    # The response mock needs to support async context manager (async with session.get() as resp)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)

    # Make response.json() a coroutine
    if not isinstance(response.json, AsyncMock):
        json_data = response.json.return_value
        response.json = AsyncMock(return_value=json_data)

    instance = MagicMock()
    instance.get = MagicMock(return_value=response)
    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
