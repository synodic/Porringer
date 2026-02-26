"""Plugin discovery helpers.

Provides `discover_plugins` which wraps entry-point discovery and
instantiation into a single canonical-name-keyed dict.  Extracted from
the sync module so that both `manifest` and `execution` can import
it without circular dependencies.
"""

import importlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import NamedTuple

from porringer.backend.builder import Builder
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Plugin

logger = logging.getLogger(__name__)


class DiscoveredPlugins(NamedTuple):
    """Result of discovering all three plugin groups at once."""

    environments: dict[str, Environment]
    project_environments: dict[str, ProjectEnvironment]
    scm_environments: dict[str, ScmEnvironment]


# ---------------------------------------------------------------------------
# Plugin cache
# ---------------------------------------------------------------------------

CACHE_TTL: float = 30.0  # seconds


@dataclass
class _PluginCache:
    """Mutable container for the in-memory plugin cache."""

    plugins: DiscoveredPlugins | None = None
    timestamp: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_cache = _PluginCache()


def invalidate_plugin_cache() -> None:
    """Clear the in-memory plugin cache.

    Call this when the process environment changes (e.g. after
    installing a new backend) so that the next
    :func:`discover_all_plugins` call performs a fresh scan.
    """
    with _cache.lock:
        _cache.plugins = None
        _cache.timestamp = 0.0


def discover_plugins[T: Plugin](group: str, base_class: type[T], **kwargs: bool) -> dict[str, T]:
    """Discover and instantiate plugins, returning a name-keyed dict.

    Calls `importlib.invalidate_caches()` before discovery so that
    distributions installed earlier in the same process (e.g. a tool
    backend installed via pip in Phase 2a) are visible to
    `importlib.metadata.entry_points()`.

    Args:
        group: Entry-point group suffix (e.g. `'environment'`).
        base_class: Expected base class for the plugins.
        **kwargs: Forwarded to `Builder.find_plugins()`
            (e.g. `check_dependencies=True`).

    Returns:
        Dict mapping canonical plugin name to instantiated plugin.
    """
    # Ensure newly-installed distributions are visible to the metadata API.
    importlib.invalidate_caches()

    infos = Builder.find_plugins(group, base_class, **kwargs)
    instances = Builder.build_plugins(infos)
    result = {info.name: inst for info, inst in zip(infos, instances, strict=True)}
    logger.info('Discovered %d %s plugin(s): %s', len(result), group, sorted(result))
    return result


def discover_all_plugins(*, use_cache: bool = False) -> DiscoveredPlugins:
    """Discover all three plugin groups in one call.

    Convenience wrapper that discovers environments (with dependency
    checking), project environments, and SCM environments, returning
    them as a :class:`DiscoveredPlugins` named tuple.

    Args:
        use_cache: When ``True``, return a cached result if one exists
            and is younger than :data:`CACHE_TTL` seconds.  Callers
            on the hot path (preview, dry-run) set this to ``True``.
            Callers that need freshness after installing packages
            (execution phase transitions) leave it ``False``.

    Returns:
        A ``DiscoveredPlugins`` with ``environments``,
        ``project_environments``, and ``scm_environments``.
    """
    with _cache.lock:
        if use_cache and _cache.plugins is not None and (time.monotonic() - _cache.timestamp) < CACHE_TTL:
            logger.debug('Plugin cache hit')
            return _cache.plugins

    result = DiscoveredPlugins(
        environments=discover_plugins('environment', Environment, check_dependencies=True),
        project_environments=discover_plugins('project_environment', ProjectEnvironment),
        scm_environments=discover_plugins('scm', ScmEnvironment),
    )
    logger.info(
        'Plugin discovery complete — environments: %s, project: %s, scm: %s',
        sorted(result.environments),
        sorted(result.project_environments),
        sorted(result.scm_environments),
    )

    with _cache.lock:
        _cache.plugins = result
        _cache.timestamp = time.monotonic()

    return result
