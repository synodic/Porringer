"""Backend resolution for mapping (kind, ecosystem) pairs to installer plugins.

The `BackendResolver` determines which plugin should handle each
`(PluginKind, ecosystem)` pair declared in a manifest.  For example,
`(PACKAGE, "python")` might resolve to `uv` or `pip` depending
on availability and user preferences.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping

from porringer.core.schema import Ecosystem, Plugin, PluginKind

logger = logging.getLogger(__name__)

# Type alias for any plugin that participates in backend resolution.
BackendPlugin = Plugin


class BackendResolver:
    """Maps `(PluginKind, ecosystem)` pairs to the best available plugin.

    Construction requires a single *plugins* mapping containing **all**
    instantiated plugins (environments, project environments, SCM
    environments) keyed by their canonical name, plus an optional
    *preferences* dict from the manifest.

    Resolution algorithm per `(kind, ecosystem)` pair:

    1. If the user gave an explicit **preference** for the ecosystem and
       the named plugin is available, use it.
    2. Otherwise filter to supported & available candidates, sort
       alphabetically by name, and pick the first one.
    """

    def __init__(
        self,
        plugins: Mapping[str, BackendPlugin],
        preferences: Mapping[Ecosystem, str] | None = None,
    ) -> None:
        """Initialize the backend resolver with available plugins and preferences.

        Args:
            plugins: All instantiated plugins keyed by canonical name.
            preferences: Optional ecosystem → plugin-name preference mapping.
        """
        self._all_plugins: dict[str, BackendPlugin] = dict(plugins)
        self._preferences = preferences or {}

        # Index: (kind, ecosystem) -> [plugin_name, ...]
        self._backend_plugins: dict[tuple[PluginKind, Ecosystem], list[str]] = defaultdict(list)
        for name, plugin in self._all_plugins.items():
            ecosystem = type(plugin).ecosystem()
            if ecosystem is not None:
                self._backend_plugins[(type(plugin).plugin_kind(), ecosystem)].append(name)

        # Resolve once and cache
        self._resolved: dict[tuple[PluginKind, Ecosystem], str | None] = {}
        for key in self._backend_plugins:
            self._resolved[key] = self._resolve(key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, kind: PluginKind, ecosystem: Ecosystem) -> str | None:
        """Return the chosen plugin name for *(kind, ecosystem)*, or `None`."""
        key = (kind, ecosystem)
        if key not in self._resolved:
            logger.warning("No plugins registered for (%s, '%s')", kind.value, ecosystem)
            return None
        return self._resolved[key]

    def validator_for(self, kind: PluginKind, ecosystem: Ecosystem) -> str | None:
        """Return the `package_name_validator()` tag for the resolved plugin.

        Returns `None` when no plugin is resolved or the plugin
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

    def _resolve(self, key: tuple[PluginKind, Ecosystem]) -> str | None:
        """Pick the best plugin for *(kind, ecosystem)*.

        1. Explicit preference (if supported & available).
        2. Sort supported & available candidates alphabetically, pick first.
        """
        kind, ecosystem = key
        candidates = self._backend_plugins.get(key, [])
        if not candidates:
            return None

        # 1. Explicit preference
        if ecosystem in self._preferences:
            preferred = self._preferences[ecosystem]
            if preferred in candidates and self._is_suitable(preferred):
                return preferred
            logger.warning(
                "Preferred plugin '%s' for (%s, '%s') is not available; falling back",
                preferred,
                kind.value,
                ecosystem,
            )

        # 2. Alphabetical among supported & available candidates
        suitable = sorted(name for name in candidates if self._is_suitable(name))
        if not suitable:
            logger.warning("No available plugin for (%s, '%s')", kind.value, ecosystem)
            return None

        if len(suitable) > 1:
            logger.info(
                "Multiple plugins available for (%s, '%s'): %s — selecting '%s'. "
                'Set a preference to choose explicitly.',
                kind.value,
                ecosystem,
                ', '.join(suitable),
                suitable[0],
            )
        return suitable[0]

    def _is_suitable(self, plugin_name: str) -> bool:
        """Check if *plugin_name* is both supported and available."""
        plugin = self._all_plugins.get(plugin_name)
        if plugin is None:
            return False
        try:
            plugin_type = type(plugin)
            if not plugin_type.is_supported():
                return False
            return plugin.is_available()
        except Exception:
            logger.warning("Suitability check failed for plugin '%s'", plugin_name, exc_info=True)
            return False
