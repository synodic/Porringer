"""WSL2 detection and helper utilities.

These functions probe the host environment to determine whether
WSL2 is available and which distributions are installed.  Results
are intentionally *not* cached at the module level — callers are
expected to cache as appropriate for their use-case.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_wsl_host() -> bool:
    """Return ``True`` when running on Windows with ``wsl.exe`` on PATH."""
    return sys.platform == 'win32' and shutil.which('wsl') is not None


def is_inside_wsl() -> bool:
    """Return ``True`` when running inside a WSL2 distribution.

    Checks ``/proc/version`` for the ``microsoft`` or ``WSL`` marker
    strings that the WSL2 kernel writes.
    """
    if sys.platform != 'linux':
        return False
    try:
        text = Path('/proc/version').read_text(encoding='utf-8', errors='replace')
        lower = text.lower()
        return 'microsoft' in lower or 'wsl' in lower
    except OSError:
        return False


def get_wsl_distro_name() -> str | None:
    """Return the name of the current WSL distribution, or ``None``.

    Inside a WSL2 session the ``WSL_DISTRO_NAME`` environment variable
    is set by the WSL runtime.  Returns ``None`` when not running
    inside WSL or the variable is absent.
    """
    return os.environ.get('WSL_DISTRO_NAME')


@functools.cache
def native_distro() -> str | None:
    """Return the current WSL distro name when running natively inside WSL, else ``None``.

    Combines :func:`is_inside_wsl` and :func:`get_wsl_distro_name`.
    Result is cached for the process lifetime since the execution
    environment cannot change.
    """
    if is_inside_wsl():
        return get_wsl_distro_name()
    return None


def available_distros() -> list[str]:
    """Return the names of all installed WSL distributions.

    Parses ``wsl --list --quiet`` output.  Returns an empty list
    when ``wsl.exe`` is not available or the command fails.
    """
    if not is_wsl_host():
        return []

    try:
        result = subprocess.run(
            ['wsl', '--list', '--quiet'],
            capture_output=True,
            timeout=10,
            check=False,
        )
        # wsl --list outputs UTF-16-LE on Windows
        text = result.stdout.decode('utf-16-le', errors='replace')
        return [line.strip() for line in text.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def wsl_which(distro: str, tool_name: str) -> bool:
    """Check whether *tool_name* exists on PATH inside *distro*.

    Runs ``wsl -d <distro> -- which <tool_name>`` and returns
    ``True`` when the exit code is 0.
    """
    try:
        result = subprocess.run(
            ['wsl', '-d', distro, '--', 'which', tool_name],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def windows_to_wsl_path(distro: str, windows_path: Path) -> str | None:
    """Translate a Windows path to its ``/mnt/...`` equivalent inside *distro*.

    Uses ``wsl -d <distro> -- wslpath <windows_path>`` for the
    translation.  Returns ``None`` on failure.
    """
    try:
        result = subprocess.run(
            ['wsl', '-d', distro, '--', 'wslpath', str(windows_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None
