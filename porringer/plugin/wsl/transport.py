"""WSL2 transport for routing commands through a WSL distribution.

``WslTransport`` implements the :class:`~porringer.core.transport.Transport`
protocol by prepending ``wsl --exec -d <distro>`` to command arguments
and translating Windows paths to their ``/mnt/...`` equivalents.
"""

from __future__ import annotations

from pathlib import Path

from porringer.plugin.wsl.utility import windows_to_wsl_path, wsl_which

__all__ = ['WslTransport']


class WslTransport:
    """Route subprocess commands through a WSL2 distribution.

    Uses ``wsl --exec`` (rather than ``wsl --``) to skip shell
    initialisation for faster command startup.

    Args:
        distro: The WSL distribution name (e.g. ``"Ubuntu-22.04"``).
    """

    __slots__ = ('_distro', '_tool_cache')

    def __init__(self, distro: str) -> None:
        """Initialise the transport for *distro*."""
        self._distro = distro
        self._tool_cache: dict[str, bool] = {}

    @property
    def distro(self) -> str:
        """The target WSL distribution name."""
        return self._distro

    def transform_args(self, args: list[str]) -> list[str]:
        """Prepend ``wsl --exec -d <distro>`` to *args*."""
        return ['wsl', '--exec', '-d', self._distro, *args]

    def transform_cwd(self, cwd: Path | None) -> Path | None:
        """Translate a Windows *cwd* to a WSL mount path.

        Returns ``None`` unchanged.  Non-Windows paths (already
        POSIX) are returned as-is since the subprocess runs inside
        WSL where the path is valid.
        """
        if cwd is None:
            return None
        # If it looks like a Windows absolute path, translate it
        cwd_str = str(cwd)
        _MIN_WINDOWS_PATH_LEN = 2
        if len(cwd_str) >= _MIN_WINDOWS_PATH_LEN and cwd_str[1] == ':':
            translated = windows_to_wsl_path(self._distro, cwd)
            return Path(translated) if translated else cwd
        return cwd

    def check_tool(self, tool_name: str) -> bool:
        """Check whether *tool_name* is available inside the WSL distro."""
        try:
            return self._tool_cache[tool_name]
        except KeyError:
            result = wsl_which(self._distro, tool_name)
            self._tool_cache[tool_name] = result
            return result

    def __repr__(self) -> str:
        """Return a developer-friendly representation."""
        return f'WslTransport(distro={self._distro!r})'
