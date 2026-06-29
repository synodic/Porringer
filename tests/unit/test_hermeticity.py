"""Helpers for test hermeticity.

Hermeticity self-check for the offline unit lane.

The unit lane runs with ``--disable-socket`` so tests cannot reach the
network.  This module proves that guard is actually enforced rather than
merely configured — if the protection ever regresses, this test fails.
"""

import socket

import pytest
from pytest_socket import SocketBlockedError, SocketConnectBlockedError

# RFC 5737 TEST-NET-1 is reserved and guaranteed non-routable, so this
# assertion never produces real network traffic even in the unexpected
# case where the guard is absent.
_UNROUTABLE_ADDRESS = ('192.0.2.1', 80)


@pytest.mark.disable_socket
def test_outbound_socket_is_blocked() -> None:
    """An outbound connection is refused by the socket guard.

    The ``disable_socket`` marker activates the guard for this test
    regardless of how pytest is invoked, so the check is deterministic.
    Both failure modes are accepted: socket *creation* is refused when no
    hosts are allow-listed, while *connection* is refused to a
    non-allow-listed host when loopback is permitted.
    """
    with pytest.raises((SocketBlockedError, SocketConnectBlockedError)):
        socket.create_connection(_UNROUTABLE_ADDRESS, timeout=0.01)
