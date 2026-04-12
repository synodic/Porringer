"""Integration tests that verify plugins tolerate a bare environment.

Every available plugin is dry-run installed under a restricted PATH
where only the plugin's primary tool is discoverable.  This catches
undeclared auxiliary-tool dependencies that would fail on a clean
machine.

Safety guarantees (no system mutation):

1. ``dry_run=True`` — the sync engine skips all mutating actions
   (install, clone, post-sync commands).  Only read-only subprocess
   calls happen (e.g. ``git rev-parse`` for presence detection,
   ``pip list`` for package queries).
2. ``bare_environment_context`` — restricts ``shutil.which`` so that
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
from tests.conftest import bare_environment_context

_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.bare_environment
@pytest.mark.fresh_plugins
class TestBareEnvironmentDryRun:
    """Dry-run the bootstrap example under a bare PATH."""

    @staticmethod
    async def test_dry_run_succeeds_under_bare_environment(session_api: API) -> None:
        """Dry-run with only primary tools on PATH should not crash."""
        setup_params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)

        with bare_environment_context(allowed_tools={'pip', 'python', 'git'}):
            results = await session_api.sync.run(setup_params)

        assert len(results.manifest_results) >= 1
        for manifest in results.manifest_results:
            # Every action should resolve without raising
            assert manifest.actions is not None
