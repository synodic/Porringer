"""Plugin discovery helpers.

Provides `discover_plugins` which wraps entry-point discovery and
instantiation into a single canonical-name-keyed dict.  Extracted from
the sync module so that both `manifest` and `execution` can import
it without circular dependencies.
"""

import importlib
from typing import NamedTuple

from porringer.backend.builder import Builder
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Plugin
from porringer.utility.utility import canonicalize_type


class DiscoveredPlugins(NamedTuple):
    """Result of discovering all three plugin groups at once."""

    environments: dict[str, Environment]
    project_environments: dict[str, ProjectEnvironment]
    scm_environments: dict[str, ScmEnvironment]


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


def discover_all_plugins() -> DiscoveredPlugins:
    """Discover all three plugin groups in one call.

    Convenience wrapper that discovers environments (with dependency
    checking), project environments, and SCM environments, returning
    them as a :class:`DiscoveredPlugins` named tuple.

    Returns:
        A ``DiscoveredPlugins`` with ``environments``,
        ``project_environments``, and ``scm_environments``.
    """
    return DiscoveredPlugins(
        environments=discover_plugins('environment', Environment, check_dependencies=True),
        project_environments=discover_plugins('project_environment', ProjectEnvironment),
        scm_environments=discover_plugins('scm', ScmEnvironment),
    )
