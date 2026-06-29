"""Helpers for test minimal path."""

"""Integration tests that verify plugins tolerate minimal executable discovery.

Every available plugin is inspected with restricted tool discovery
where only the plugin's primary tool is discoverable.  This catches
undeclared auxiliary-tool dependencies that would fail on a clean
machine.

Safety guarantees (no system mutation):

1. ``sync.inspect`` — the sync engine does not execute mutating actions
   (install, clone). Only read-only subprocess calls
   happen (e.g. ``git rev-parse`` for presence detection, ``pip list``
   for package queries).
2. ``minimal_path_context`` — restricts ``shutil.which`` so that
   even if a mutating code path were accidentally reached, most tools
   would appear absent and the operation would be skipped or fail
   before spawning a process.
3. ``session_api`` — uses an isolated temporary directory tree for
   configuration and cache (no writes to user/system porringer dirs).
"""

from pathlib import Path

import pytest

from porringer.api import API
from porringer.schema import SetupParameters
from tests.conftest import minimal_path_context

_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.fresh_plugins
class TestMinimalPathInspect:
    """Inspect the bootstrap example with minimal tool discovery."""

    @staticmethod
    async def test_inspect_succeeds_under_minimal_path(session_api: API) -> None:
        """Inspection with only declared tools visible should not crash."""
        setup_params = SetupParameters(paths=_BOOTSTRAP_DIR)

        with minimal_path_context(allowed_tools={'pip', 'python', 'git'}):
            report = await session_api.sync.inspect(setup_params)

        assert len(report.manifests) >= 1
        for manifest in report.manifests:
            assert manifest.actions is not None
