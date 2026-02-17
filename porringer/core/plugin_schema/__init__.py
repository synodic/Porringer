"""Plugin schema package for Porringer.

This package contains the schema definitions and base classes for plugins,
including environment plugins and their parameters.
"""

from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.plugin_manager import PluginManager

__all__ = ['ManifestContributor', 'PluginManager']
