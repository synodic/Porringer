"""Protocols for runtime provider and consumer plugins.

Plugins that manage interpreter installations (e.g. pim, pyenv) implement
`RuntimeProvider` so the sync engine can resolve the filesystem path
to a managed interpreter.  Plugins that need a runtime (e.g. pip, uv, pipx)
implement `RuntimeConsumer` so the engine knows where to propagate
the resolved path.

The two-part protocol system keeps phasing logic entirely protocol-driven —
the sync engine never needs to match on backend-name strings to decide
which plugins provide or consume runtimes.

Runtime state is held externally in a :class:`RuntimeContext`, not on
plugin instances.  This keeps cached plugin objects stateless and
prevents mutation from leaking across execution boundaries.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Runtime context — externalised runtime state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuntimeContext:
    """Resolved runtime executables for a single execution run.

    Owned by ``ExecutionState``; passed explicitly to every plugin
    method that needs to know which interpreter to target.  Plugin
    instances themselves never store runtime state.

    ``executables`` maps a *kind* string (e.g. ``"python"``) to the
    resolved filesystem path of the managed interpreter.
    """

    executables: dict[str, Path] = field(default_factory=dict)

    def get(self, kind: str) -> Path | None:
        """Return the resolved executable for *kind*, or ``None``."""
        return self.executables.get(kind)


# ---------------------------------------------------------------------------
# Provider / consumer protocols
# ---------------------------------------------------------------------------


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

    @abstractmethod
    async def available_tags(self) -> list[str]:
        """Return version tags this provider can resolve.

        Unlike the ``packages()`` query on
        :class:`~porringer.core.plugin_schema.environment.Environment`
        (which returns only actively managed installs), this method
        probes the system for **all** tags the underlying tool can
        dispatch to — including runtimes installed by other tools.

        :meth:`Builder.resolve_runtime_context
        <porringer.backend.builder.Builder.resolve_runtime_context>`
        calls this instead of ``packages()`` so that interpreters
        installed via the official installer, Microsoft Store, or
        other sources are still discoverable for downstream
        ``RuntimeConsumer`` plugins.

        Returns:
            A list of version tag strings (e.g.
            ``["3.14", "3.12", "3.11"]``).  May be empty when no
            runtime is available.
        """
        ...


@runtime_checkable
class RuntimeConsumer(Protocol):
    """A plugin that consumes a resolved runtime executable.

    Plugins that operate against a specific language runtime (pip, uv,
    pipx, PDM, Poetry, etc.) implement this protocol so the sync
    engine knows which runtime *kind* to supply via
    :class:`RuntimeContext`.

    Runtime state is **not** stored on the plugin instance.  Instead,
    a ``RuntimeContext`` is passed to every method that needs it.
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

    @classmethod
    def is_available_for(cls, runtime_context: RuntimeContext) -> bool:
        """Check whether this plugin can operate with *runtime_context*.

        Called during **deferred resolution** — after a preceding phase
        has resolved a runtime and injected it onto ``PATH``.  This
        gives plugins an opportunity to probe the *target* interpreter
        rather than only checking the host process.

        The default implementation delegates to the class-level
        ``is_available()`` (if present) so that plugins which do not
        need runtime-aware probing continue to work unchanged.

        Subclasses should override when their availability depends on
        the resolved runtime (e.g. ``pip`` checking whether
        ``python -m pip`` works with the target interpreter).

        Args:
            runtime_context: The resolved runtime executables for the
                current execution run.

        Returns:
            ``True`` if the plugin can handle operations with the
            given runtime, ``False`` otherwise.
        """
        # Default: fall back to the class-level is_available() when
        # the concrete type defines one (all ToolBasedPlugin subclasses do).
        if hasattr(cls, 'is_available'):
            return cls.is_available()
        return True
