"""Backend resolution for mapping (kind, ecosystem) pairs to installer plugins.

The ``BackendResolver`` determines which plugin should handle each
``(PluginKind, ecosystem)`` pair declared in a manifest.  For example,
``(PACKAGE, "python")`` might resolve to ``uv`` or ``pip`` depending
on availability and user preferences.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import PluginKind

logger = logging.getLogger(__name__)

# Type alias for any plugin that participates in backend resolution.
BackendPlugin = Environment | ProjectEnvironment


class BackendResolver:
    """Maps ``(PluginKind, ecosystem)`` pairs to the best available plugin.

    Construction requires two inputs:

    * *environments* – all instantiated :class:`Environment` plugins
      keyed by their canonical name.
    * *preferences* – an optional dict coming from the manifest's
      ``preferences`` field (e.g. ``{"python": "uv"}``).

    Optionally accepts *project_environments* for project-scoped plugins.

    Resolution algorithm per ``(kind, ecosystem)`` pair:

    1. If the user gave an explicit **preference** for the ecosystem and
       the named plugin is available, use it.
    2. Otherwise sort all registered candidates by
       :meth:`~Plugin.default_priority` ascending and pick the first one
       whose ``is_available()`` returns ``True``.
    """

    def __init__(
        self,
        environments: Mapping[str, Environment],
        preferences: Mapping[str, str] | None = None,
        project_environments: Mapping[str, ProjectEnvironment] | None = None,
    ) -> None:
        """Initialize the backend resolver with available plugins and preferences.

        Args:
            environments: Instantiated environment plugins keyed by name.
            preferences: Optional ecosystem → plugin-name preference mapping.
            project_environments: Optional instantiated project-environment plugins.
        """
        self._environments = environments
        self._project_environments: Mapping[str, ProjectEnvironment] = project_environments or {}
        self._preferences = preferences or {}

        # Merged view for indexing and availability checks
        self._all_plugins: dict[str, BackendPlugin] = dict(environments)
        self._all_plugins.update(self._project_environments)

        # Index: (kind, ecosystem) -> [plugin_name, ...]
        self._backend_plugins: dict[tuple[PluginKind, str], list[str]] = {}
        for name, plugin in self._all_plugins.items():
            ecosystem = type(plugin).ecosystem()
            if ecosystem is not None:
                kind = type(plugin).plugin_kind()
                key = (kind, ecosystem)
                self._backend_plugins.setdefault(key, []).append(name)

        # Resolve once and cache
        self._resolved: dict[tuple[PluginKind, str], str | None] = {}
        for key in self._backend_plugins:
            self._resolved[key] = self._resolve(key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, kind: PluginKind, ecosystem: str) -> str | None:
        """Return the chosen plugin name for *(kind, ecosystem)*, or ``None``."""
        key = (kind, ecosystem)
        if key not in self._resolved:
            logger.warning("No plugins registered for (%s, '%s')", kind.value, ecosystem)
            return None
        return self._resolved[key]

    def validator_for(self, kind: PluginKind, ecosystem: str) -> str | None:
        """Return the ``package_name_validator()`` tag for the resolved plugin.

        Returns ``None`` when no plugin is resolved or the plugin
        declares no validator.
        """
        name = self.resolve(kind, ecosystem)
        if name is None:
            return None
        plugin = self._all_plugins.get(name)
        if plugin is None:
            return None
        return type(plugin).package_name_validator()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, key: tuple[PluginKind, str]) -> str | None:
        """Pick the best plugin for *(kind, ecosystem)*.

        1. Explicit preference (if available).
        2. Sort candidates by ``default_priority()`` ascending, pick first available.
        """
        kind, ecosystem = key
        candidates = self._backend_plugins.get(key, [])
        if not candidates:
            return None

        # 1. Explicit preference
        if ecosystem in self._preferences:
            preferred = self._preferences[ecosystem]
            if preferred in candidates and self._is_available(preferred):
                return preferred
            logger.warning(
                "Preferred plugin '%s' for (%s, '%s') is not available; falling back",
                preferred,
                kind.value,
                ecosystem,
            )

        # 2. Sort by default_priority ascending
        def _priority(name: str) -> int:
            plugin = self._all_plugins.get(name)
            if plugin is None:
                return 9999
            try:
                return type(plugin).default_priority()
            except Exception:
                return 9999

        sorted_candidates = sorted(candidates, key=_priority)
        for name in sorted_candidates:
            if self._is_available(name):
                return name

        logger.warning("No available plugin for (%s, '%s')", kind.value, ecosystem)
        return None

    def _is_available(self, plugin_name: str) -> bool:
        """Check if *plugin_name* reports itself as available."""
        plugin = self._all_plugins.get(plugin_name)
        if plugin is None:
            return False
        try:
            return type(plugin).is_available()
        except Exception:
            logger.debug("is_available() failed for plugin '%s'", plugin_name, exc_info=True)
            return False
