"""Porringer package.

This package provides the core functionality for the Porringer application,
including API definitions, backend processing, console commands, and utility functions.
"""

import logging

# Library convention: add NullHandler so that consuming applications
# can attach their own handlers to the "porringer" logger hierarchy.
# See https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library
logging.getLogger(__name__).addHandler(logging.NullHandler())
