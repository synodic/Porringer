"""Generalized phase abstraction for the execution pipeline.

Each phase in the setup flow (runtime → packages → tools → project-sync →
SCM → post-sync commands) is represented by a :class:`Phase` instance that
encapsulates the *refresh → resolve-deferred → execute → post-hook* cycle.

The :func:`run_phases` driver replaces the hand-coded per-phase blocks in
``execute_single()`` with a uniform loop.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override

from porringer.core.schema import PluginKind
from porringer.schema import SetupActionResult

if TYPE_CHECKING:
    from .execution import ExecutionState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """Outcome of a single phase execution.

    Attributes:
        results: Per-action results produced by this phase.
        should_continue: ``False`` when fail-fast was triggered and the
            pipeline should abort immediately.
    """

    results: list[SetupActionResult]
    should_continue: bool = True


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


class Phase(Protocol):
    """Unified interface for every execution phase.

    Implementors define *kind* (which action bucket they handle),
    *refresh* (plugin re-discovery), *execute* (the actual work), and
    *post_execute* (hooks that run after a successful phase).
    """

    @property
    def kind(self) -> PluginKind | None:
        """The ``PluginKind`` this phase processes (``None`` for post-sync commands)."""
        ...

    async def refresh(self, state: ExecutionState) -> None:
        """Re-discover plugins and resolve deferred actions for **this** phase only.

        Called *before* :meth:`execute`.  The default implementation is a no-op.
        """
        ...

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Run the phase's actions and return aggregated results.

        Must return a :class:`PhaseResult`.  Set ``should_continue=False``
        to abort the pipeline (fail-fast).
        """
        ...

    async def post_execute(self, state: ExecutionState) -> None:
        """Hook called *after* a successful execution (``should_continue=True``).

        Typical use: propagate a resolved runtime to downstream consumers.
        The default implementation is a no-op.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete phases
# ---------------------------------------------------------------------------


class _PhaseBase:
    """Shared helpers for concrete phase implementations."""

    _kind: PluginKind | None

    @property
    def kind(self) -> PluginKind | None:
        return self._kind

    # Default no-ops — subclasses override as needed.
    async def refresh(self, state: ExecutionState) -> None:
        """Re-discover plugins (no-op by default)."""

    async def post_execute(self, state: ExecutionState) -> None:
        """Run post-execution hooks (no-op by default)."""


class RuntimePhase(_PhaseBase):
    """Phase 1: install / resolve language runtimes (pim, pyenv)."""

    _kind = PluginKind.RUNTIME

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Install and resolve language runtimes."""
        results, ok = await state.run_package_actions(state.phases[self._kind])
        return PhaseResult(results=results, should_continue=ok)

    @override
    async def post_execute(self, state: ExecutionState) -> None:
        """Propagate the resolved runtime and trigger a full plugin refresh."""
        await state.propagate_runtime()
        await state._propagate_wsl_runtimes()
        await asyncio.to_thread(state.refresh_all_plugins)


class PackagePhase(_PhaseBase):
    """Phase 2a: install packages into the current environment (pip, uv)."""

    _kind = PluginKind.PACKAGE

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Install packages into the current environment."""
        results, ok = await state.run_package_actions(state.phases[self._kind])
        return PhaseResult(results=results, should_continue=ok)


class ToolPhase(_PhaseBase):
    """Phase 2b: install isolated CLI tools (pipx, etc.)."""

    _kind = PluginKind.TOOL

    @override
    async def refresh(self, state: ExecutionState) -> None:
        """Re-discover plugins so that tools installed in Phase 2a become available."""
        await asyncio.to_thread(state.refresh_all_plugins)

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Install isolated CLI tools."""
        results, ok = await state.run_package_actions(state.phases[self._kind])
        return PhaseResult(results=results, should_continue=ok)


class ProjectPhase(_PhaseBase):
    """Phase 3: run project-sync (pdm install, uv sync, etc.)."""

    _kind = PluginKind.PROJECT

    @override
    async def refresh(self, state: ExecutionState) -> None:
        """Re-discover all plugins so that project environments installed in earlier phases are available."""
        await asyncio.to_thread(state.refresh_all_plugins)

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Run project sync actions."""
        results = await state.run_project_phase(state.phases[self._kind])
        failed = any(not r.success and not r.skipped for r in results)
        ok = not (failed and state.parameters.fail_fast)
        return PhaseResult(results=results, should_continue=ok)


class ScmPhase(_PhaseBase):
    """Phase 4: clone source-control repositories."""

    _kind = PluginKind.SCM

    @override
    async def refresh(self, state: ExecutionState) -> None:
        """Re-discover plugins so that SCM tools installed in earlier phases are available."""
        await asyncio.to_thread(state.refresh_all_plugins)

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Clone source-control repositories."""
        results = await state.run_scm_actions(state.phases[self._kind])
        failed = any(not r.success and not r.skipped for r in results)
        ok = not (failed and state.parameters.fail_fast)
        return PhaseResult(results=results, should_continue=ok)


class CommandPhase(_PhaseBase):
    """Phase 5: run arbitrary post-sync shell commands."""

    _kind = None

    async def execute(self, state: ExecutionState) -> PhaseResult:
        """Run post-sync shell commands."""
        results = await state.run_command_actions(state.phases[self._kind])
        failed = any(not r.success and not r.skipped for r in results)
        ok = not (failed and state.parameters.fail_fast)
        return PhaseResult(results=results, should_continue=ok)


# ---------------------------------------------------------------------------
# Canonical phase ordering
# ---------------------------------------------------------------------------

PHASES: list[Phase] = [
    RuntimePhase(),
    PackagePhase(),
    ToolPhase(),
    ProjectPhase(),
    ScmPhase(),
    CommandPhase(),
]
"""The ordered list of phases that :func:`run_phases` iterates."""


# ---------------------------------------------------------------------------
# Phase loop driver
# ---------------------------------------------------------------------------


async def run_phases(state: ExecutionState) -> None:
    """Execute all non-empty phases in order.

    For each phase:

    1. **Skip** if the phase bucket is empty.
    2. **Refresh** — re-discover plugins and resolve deferred actions
       (scoped to this phase only).
    3. **Execute** — run the phase's actions and collect results.
    4. **Post-execute** — run post-hooks (e.g. runtime propagation)
       only when the phase completed without aborting the pipeline.
    """
    for phase in PHASES:
        actions = state.phases.get(phase.kind)
        if not actions:
            continue

        # 1. Refresh plugins
        await phase.refresh(state)

        # 2. Resolve deferred actions for THIS phase only
        await asyncio.to_thread(state.resolve_deferred, actions)

        # 3. Execute
        result = await phase.execute(state)
        state.results.extend(result.results)

        if not result.should_continue:
            return

        # 4. Post-execute hooks
        await phase.post_execute(state)
