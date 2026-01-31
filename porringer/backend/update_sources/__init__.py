"""Update sources module.

This module previously contained update source adapters for checking versions.
Update checking is now delegated to individual plugins via their check_updates() method.
"""
