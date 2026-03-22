"""WSL transport overlay helpers.

Provides :func:`overlay_wsl_plugin` and :func:`wsl_transport_for`
which wrap plugin instances with a WSL transport when execution
targets a WSL2 distribution.

Extracted from ``execution`` to avoid circular imports with
``presence``.
"""

from __future__ import annotations

from porringer.core.schema import Plugin
from porringer.plugin.wsl.transport import WslTransport
from porringer.plugin.wsl.utility import native_distro


def wsl_transport_for(distro: str) -> WslTransport | None:
    """Return a ``WslTransport`` for *distro*, or ``None`` when already inside that distro."""
    if native_distro() == distro:
        return None
    return WslTransport(distro)


def overlay_wsl_plugin[P: Plugin](
    plugins: dict[str, P],
    installer: str,
    distro: str,
) -> dict[str, P]:
    """Return a shallow copy of *plugins* with *installer* swapped to a WSL-transport variant.

    When already running inside the target distro the dict is returned
    unchanged — local execution is used instead of ``wsl --exec``.

    The original dict is never mutated.
    """
    transport = wsl_transport_for(distro)
    if transport is None:
        return plugins
    base = plugins[installer]
    wsl_plugin = base.with_transport(transport)
    return {**plugins, installer: wsl_plugin}
