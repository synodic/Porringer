"""Transport abstraction for routing subprocess calls.

A ``Transport`` transforms command arguments and working directories
before they are passed to ``asyncio.create_subprocess_exec``.  This
allows the same plugin code to execute commands locally, inside a
WSL2 distro, or (in the future) inside a Docker container — without
any changes to the plugin itself.

Every plugin instance carries a transport (defaulting to
``LocalTransport``).  The three ``_run_*_command`` helpers and
``_execute_command`` apply the transport before launching the
subprocess.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ['LocalTransport', 'Transport']


@runtime_checkable
class Transport(Protocol):
    """Protocol for subprocess command routing.

    Implementations transform command arguments and working directories
    so that subprocesses can target a different execution environment
    (WSL2 distro, Docker container, SSH host, etc.).
    """

    def transform_args(self, args: list[str]) -> list[str]:
        """Transform command arguments before subprocess launch.

        Args:
            args: Original command and arguments.

        Returns:
            Potentially modified command and arguments.
        """
        ...

    def transform_cwd(self, cwd: Path | None) -> Path | None:
        """Transform the working directory for the subprocess.

        Args:
            cwd: Original working directory, or ``None``.

        Returns:
            Potentially translated path, or ``None``.
        """
        ...

    def check_tool(self, tool_name: str) -> bool:
        """Check whether *tool_name* is available in the target environment.

        Args:
            tool_name: The CLI executable name to probe.

        Returns:
            ``True`` if the tool is available.
        """
        ...


class LocalTransport:
    """Passthrough transport for local subprocess execution.

    All methods are identity operations — commands run on the host
    exactly as before.
    """

    __slots__ = ()

    def transform_args(self, args: list[str]) -> list[str]:
        return args

    def transform_cwd(self, cwd: Path | None) -> Path | None:
        return cwd

    def check_tool(self, tool_name: str) -> bool:
        return shutil.which(tool_name) is not None
