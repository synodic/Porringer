"""Plugin discovery helpers.

Provides `discover_plugins` which wraps entry-point discovery and
instantiation into a single canonical-name-keyed dict.  Extracted from
the sync module so that both `manifest` and `execution` can import
it without circular dependencies.
"""

from __future__ import annotations

import importlib

from porringer.backend.builder import Builder
from porringer.core.schema import Plugin
from porringer.utility.utility import canonicalize_type


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
    return {canonicalize_type(type(inst)).name: inst for inst in instances}
