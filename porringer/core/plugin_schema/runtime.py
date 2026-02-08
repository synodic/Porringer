"""Protocol for plugins that provide Python runtime resolution.

Plugins that manage Python interpreter installations (e.g. pim, pyenv)
implement this protocol so the sync engine can resolve the filesystem
path to a managed interpreter.  This path is then forwarded to package
installers (pip, uv) so they operate on the correct environment.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeProvider(Protocol):
    """A plugin that can resolve a managed Python interpreter path.

    This is a separate protocol from :class:`Environment` — only plugins
    that manage Python runtimes (``package_backend() == "python-runtime"``)
    need to implement it.

    The sync engine checks ``isinstance(env, RuntimeProvider)`` after
    executing ``python-runtime`` actions, then calls
    :meth:`resolve_executable` to obtain the interpreter path for
    downstream backends (``python``, ``python-tool``).
    """

    @abstractmethod
    def resolve_executable(self, tag: str) -> Path | None:
        """Return the filesystem path to the Python interpreter for *tag*.

        Args:
            tag: The runtime version tag as declared in the manifest
                 (e.g. ``"3.14"``, ``"3.12"``).

        Returns:
            Absolute path to the ``python`` executable, or ``None`` if
            the runtime is not installed or the path cannot be determined.
        """
        ...
