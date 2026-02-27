"""Protocols for runtime provider and consumer plugins.

Plugins that manage interpreter installations (e.g. pim, pyenv) implement
`RuntimeProvider` so the sync engine can resolve the filesystem path
to a managed interpreter.  Plugins that need a runtime (e.g. pip, uv, pipx)
implement `RuntimeConsumer` so the engine knows where to propagate
the resolved path.

The two-part protocol system keeps phasing logic entirely protocol-driven —
the sync engine never needs to match on backend-name strings to decide
which plugins provide or consume runtimes.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeProvider(Protocol):
    """A plugin that can resolve a managed interpreter path.

    Only plugins that manage language runtimes need to implement this.
    The sync engine checks `isinstance(env, RuntimeProvider)` after
    executing runtime actions, then calls `resolve_executable()` to
    obtain the interpreter path for downstream `RuntimeConsumer`
    plugins.
    """

    @classmethod
    @abstractmethod
    def provided_runtime_kind(cls) -> str:
        """Return the kind of runtime this provider supplies.

        Used to scope propagation — e.g. a Python runtime provider
        returns `"python"` and only consumers that declare
        `consumed_runtime_kind() == "python"` receive the resolved
        executable.

        Returns:
            A runtime kind identifier (e.g. `"python"`).
        """
        ...

    @abstractmethod
    async def resolve_executable(self, tag: str) -> Path | None:
        """Return the filesystem path to the interpreter for *tag*.

        Args:
            tag: The runtime version tag as declared in the manifest
                 (e.g. `"3.14"`, `"3.12"`).

        Returns:
            Absolute path to the interpreter executable, or `None` if
            the runtime is not installed or the path cannot be determined.
        """
        ...


@runtime_checkable
class RuntimeConsumer(Protocol):
    """A plugin that accepts a runtime executable override.

    Plugins that operate against a specific language runtime (pip, uv,
    pipx, PDM, Poetry, etc.) implement this protocol so the sync engine
    can propagate the resolved interpreter after runtime providers finish.
    """

    runtime_executable: Path | None
    """The resolved runtime path, set by the sync engine after a
    `RuntimeProvider` completes.  `None` when no override is active.
    """

    @classmethod
    @abstractmethod
    def consumed_runtime_kind(cls) -> str:
        """Return the kind of runtime this consumer requires.

        Must match a `RuntimeProvider.provided_runtime_kind()` value
        for propagation to occur.

        Returns:
            A runtime kind identifier (e.g. `"python"`).
        """
        ...
