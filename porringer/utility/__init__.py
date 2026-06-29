"""Public package exports for the utility module.

Utility package for Porringer.

This package contains utility functions and classes used throughout the Porringer application,
including subprocess management, type handling, and exception definitions.
"""

import aiohttp

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10.0)
"""Default timeout for all outgoing HTTP requests (registry queries, PyPI, etc.)."""
