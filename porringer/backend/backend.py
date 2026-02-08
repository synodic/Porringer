"""Backend resolution for mapping package backends to installer plugins.

The ``BackendResolver`` determines which plugin should handle each package
backend declared in a manifest.  For example, ``"python"`` might resolve to
``pip`` or ``uv`` depending on availability and user preferences.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment

logger = logging.getLogger(__name__)

# Type alias for any plugin that participates in backend resolution.
BackendPlugin = Environment | ProjectEnvironment


def _platform_system_order() -> list[str]:
    """Return the preferred system-backend plugin order for the current platform.

    On Windows ``winget`` is the native package manager and is tried first.
    On macOS ``brew`` is the de-facto standard.
    On Linux the distribution package manager (``apt``) is preferred with
    ``brew`` (linuxbrew) as a fallback.
    """
    if sys.platform == 'win32':
        return ['winget']
    if sys.platform == 'darwin':
        return ['brew']
    # Linux / other POSIX
    return ['apt', 'brew']


def _platform_runtime_order() -> list[str]:
    """Return the preferred python-runtime plugin order for the current platform.

    On Windows, ``pim`` (Python Install Manager / pymanager) is the
    recommended tool.  On Linux and macOS, ``pyenv`` is the de-facto
    standard.
    """
    if sys.platform == 'win32':
        return ['pim']
    return ['pyenv']


# Centralized default preference order per backend.
#
# When no explicit ``preferences`` are provided in the manifest, the
# resolver walks this list in order and picks the first plugin that is
# both installed **and** available (``is_available() == True``).
DEFAULT_PREFERENCE_ORDER: dict[str, list[str]] = {
    'python': ['uv', 'pip'],
    'python-tool': ['pipx'],
    'python-project': ['uv-project', 'pdm', 'poetry'],
    'system': _platform_system_order(),
    'node': ['pnpm', 'npm', 'bun'],
    'node-project': ['pnpm-project', 'npm-project', 'yarn-project', 'bun-project'],
    'deno': ['deno'],
    'deno-project': ['deno-project'],
    'python-runtime': _platform_runtime_order(),
}


class BackendResolver:
    """Maps backend identifiers to the best available installer plugin.

    Construction requires two inputs:

    * *environments* – all instantiated :class:`Environment` plugins
      keyed by their canonical name.
    * *preferences* – an optional dict coming from the manifest's
      ``preferences`` field (e.g. ``{"python": "uv"}``).

    Optionally accepts *project_environments* for project-scoped plugins.
    The resolver builds a mapping from every declared backend to the
    chosen plugin name, falling back to :data:`DEFAULT_PREFERENCE_ORDER`
    for backends without an explicit preference.
    """

    def __init__(
        self,
        environments: Mapping[str, Environment],
        preferences: Mapping[str, str] | None = None,
        project_environments: Mapping[str, ProjectEnvironment] | None = None,
    ) -> None:
        """Initialise the resolver with available environment plugins.

        Args:
            environments: Mapping of plugin name to Environment instance.
            preferences: Optional explicit backend-to-installer overrides.
            project_environments: Optional mapping of project-environment plugins.
        """
        self._environments = environments
        self._project_environments: Mapping[str, ProjectEnvironment] = project_environments or {}
        self._preferences = preferences or {}

        # Merged view for backend indexing and availability checks
        self._all_plugins: dict[str, BackendPlugin] = dict(environments)
        self._all_plugins.update(self._project_environments)

        # Index: backend -> [plugin_name, ...] ordered by pref
        self._backend_plugins: dict[str, list[str]] = {}
        for name, plugin in self._all_plugins.items():
            backend = type(plugin).package_backend()
            if backend is not None:
                self._backend_plugins.setdefault(backend, []).append(name)

        # Resolve once and cache
        self._resolved: dict[str, str | None] = {}
        for backend in self._backend_plugins:
            self._resolved[backend] = self._resolve(backend)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, backend: str) -> str | None:
        """Return the chosen plugin name for *backend*, or ``None``."""
        if backend not in self._resolved:
            logger.warning("No plugins registered for backend '%s'", backend)
            return None
        return self._resolved[backend]

    def available_backends(self) -> set[str]:
        """Return the set of backends that have at least one plugin."""
        return set(self._backend_plugins)

    def plugins_for_backend(self, backend: str) -> list[str]:
        """Return all plugin names registered for *backend*."""
        return list(self._backend_plugins.get(backend, []))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, backend: str) -> str | None:
        """Pick the best plugin for *backend*.

        1. If the user gave an explicit preference **and** the plugin is
           available, use it.
        2. Otherwise walk ``DEFAULT_PREFERENCE_ORDER[backend]`` (or the
           fallback list of registered plugins) and pick the first one
           whose ``is_available()`` returns ``True``.
        """
        candidates = self._backend_plugins.get(backend, [])
        if not candidates:
            return None

        # 1. Explicit preference
        if backend in self._preferences:
            preferred = self._preferences[backend]
            if preferred in candidates and self._is_available(preferred):
                return preferred
            logger.warning(
                "Preferred plugin '%s' for backend '%s' is not available; falling back",
                preferred,
                backend,
            )

        # 2. Default preference order
        order = DEFAULT_PREFERENCE_ORDER.get(backend, candidates)
        for name in order:
            if name in candidates and self._is_available(name):
                return name

        # 3. Fallback: any registered candidate that is available
        for name in candidates:
            if self._is_available(name):
                return name

        logger.warning("No available plugin for backend '%s'", backend)
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
