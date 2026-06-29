"""Structured-concurrency helpers.

Provides a single sanctioned primitive for running independent async
work with bounded concurrency and isolated failures.  Prefer this over
raw :func:`asyncio.gather` so that fan-out is always bounded and one
task's failure never cancels its siblings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

__all__ = ['gather_bounded']


async def gather_bounded[T](
    factories: Iterable[Callable[[], Awaitable[T]]],
    *,
    limit: int,
) -> list[T]:
    """Run independent coroutines under a concurrency limit.

    Each element of *factories* is a zero-argument callable that returns
    a fresh awaitable when invoked.  Awaitables are scheduled inside an
    :class:`asyncio.TaskGroup` and gated by an :class:`asyncio.Semaphore`
    so that at most *limit* run concurrently.

    Results are returned in the same order as *factories*.  Unlike a bare
    :func:`asyncio.gather`, callers are expected to handle per-item
    failures themselves (typically by having each factory return a result
    object rather than raising); any exception that does escape a factory
    propagates through the ``TaskGroup`` as usual.

    Args:
        factories: Zero-argument callables producing the awaitables to run.
        limit: Maximum number of awaitables to run concurrently.  A value
            ``<= 0`` means unbounded (one task per factory).

    Returns:
        The list of results, ordered to match *factories*.
    """
    factory_list = list(factories)
    if not factory_list:
        return []

    effective_limit = len(factory_list) if limit <= 0 else min(limit, len(factory_list))
    semaphore = asyncio.Semaphore(effective_limit)

    async def _run(make: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await make()

    async with asyncio.TaskGroup() as task_group:
        tasks = [task_group.create_task(_run(make)) for make in factory_list]

    return [task.result() for task in tasks]
