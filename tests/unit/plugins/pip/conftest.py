"""Shared fixtures for pip plugin tests."""

from packaging.version import Version

from porringer.core.schema import Distribution, PluginParameters

_MOCK_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
