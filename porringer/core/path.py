"""Platform-aware PATH synchronization.

Long-running processes (desktop apps, services) may inherit a ``PATH``
that is stale relative to the current OS state.  On Windows, well-behaved
installers (e.g. the Node.js MSI) write their directories to the
registry, but the running process never sees them because
``os.environ['PATH']`` was snapshotted at process creation time.

This module provides :func:`ensure_system_path`, which reads the
**authoritative** ``PATH`` from the operating system and merges any
missing entries into the current process's ``PATH``.  It is safe to call
from any thread and is idempotent — only the first invocation performs
I/O; subsequent calls are no-ops.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:  # not on Windows
    winreg: Any = None

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {'synced': False}


# ---------------------------------------------------------------------------
# Windows: read the authoritative PATH from the registry
# ---------------------------------------------------------------------------


def read_registry_path() -> list[str]:
    """Read and expand both system and user ``PATH`` from the Windows registry.

    Returns a combined list of directories (system first, then user).
    Entries containing unexpanded ``%VAR%`` references are expanded
    via :func:`os.path.expandvars`.
    """
    entries: list[str] = []

    # System PATH
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
            0,
            winreg.KEY_READ,
        ) as key:
            raw, _ = winreg.QueryValueEx(key, 'Path')
            entries.extend(os.path.expandvars(raw).split(os.pathsep))
    except OSError:
        logger.debug('Could not read system PATH from registry')

    # User PATH
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            'Environment',
            0,
            winreg.KEY_READ,
        ) as key:
            raw, _ = winreg.QueryValueEx(key, 'Path')
            entries.extend(os.path.expandvars(raw).split(os.pathsep))
    except OSError:
        logger.debug('Could not read user PATH from registry')

    return [e for e in entries if e]


# ---------------------------------------------------------------------------
# Unix: probe well-known tool directories
# ---------------------------------------------------------------------------


def probe_unix_paths() -> list[str]:
    """Return well-known tool directories that exist on disk.

    These cover standard locations for tools installed outside of
    the user's shell profile (Homebrew, pip --user, Bun, Deno, etc.).
    Only directories that actually exist are returned.
    """
    home = Path.home()
    candidates = [
        '/usr/local/bin',
        str(home / '.local' / 'bin'),
        str(home / '.bun' / 'bin'),
        str(home / '.deno' / 'bin'),
        str(home / '.volta' / 'bin'),
        str(home / '.cargo' / 'bin'),
    ]

    if sys.platform == 'darwin':
        # Homebrew on Apple Silicon vs Intel
        candidates.append('/opt/homebrew/bin')
        candidates.append('/opt/homebrew/sbin')

    return [d for d in candidates if os.path.isdir(d)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_system_path() -> None:
    """Synchronize the process ``PATH`` with the operating system's authoritative state.

    On **Windows**, reads the current system and user ``PATH`` from the
    Windows registry (``HKLM`` and ``HKCU``) and merges any entries that
    are missing from the running process's ``os.environ['PATH']``.  This
    handles tools whose installers wrote to the registry after the
    current process was started (or whose entries were never inherited
    from the desktop shell).

    On **Unix / macOS**, probes a set of well-known directories
    (``/usr/local/bin``, ``~/.local/bin``, Homebrew prefix, etc.) and
    prepends any that exist on disk but are absent from ``PATH``.

    The function is **idempotent** — only the first call performs I/O.
    Subsequent calls return immediately.  It is safe to call from any
    thread.
    """
    # Fast path: already synchronized.
    if _state['synced']:
        return

    with _lock:
        # Double-check under lock.
        if _state['synced']:
            return

        new_dirs = read_registry_path() if os.name == 'nt' else probe_unix_paths()

        if not new_dirs:
            _state['synced'] = True
            return

        current = os.environ.get('PATH', '')
        # Normalize for case-insensitive comparison on Windows.
        if os.name == 'nt':
            current_set = {p.lower() for p in current.split(os.pathsep) if p}
            missing = [d for d in new_dirs if d.lower() not in current_set]
        else:
            current_set = set(current.split(os.pathsep))
            missing = [d for d in new_dirs if d not in current_set]

        if missing:
            os.environ['PATH'] = os.pathsep.join(missing) + os.pathsep + current
            logger.debug('System PATH contributed %d entr(ies): %s', len(missing), ', '.join(missing))
        else:
            logger.debug('System PATH already in sync')

        _state['synced'] = True


def reset_sync_state() -> None:
    """Reset the synchronization flag so :func:`ensure_system_path` will re-read.

    Intended for testing and for long-running processes that want to
    periodically re-synchronize (e.g. after a tool install phase).
    """
    with _lock:
        _state['synced'] = False
