"""Mock package for Porringer tests.

This package contains mock implementations and utilities for testing Porringer plugins and environments.
"""

from porringer.test.mock.plugin_manager import MockPluginManager
from porringer.test.mock.subprocess import fake_proc

__all__ = ['MockPluginManager', 'fake_proc']
