"""Presence test using the python-bootstrap example manifest.

Verifies that when runtimes, packages, and tools declared in
``examples/python-bootstrap/porringer.json`` are already installed,
a dry-run reports every installable action as skipped.

SCM clone actions are excluded from the all-skipped check because
their presence depends on a local filesystem path rather than an
installed package.  When SCM actions are not skipped, the post-sync
``RUN_COMMAND`` is also not skipped (something changed).
"""

from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import SetupActionResult, SetupParameters
from tests.conftest import execute_via_stream

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


class TestBootstrapPresence:
    """Dry-run the python-bootstrap example and verify all-skipped."""

    @staticmethod
    @pytest.fixture
    def dry_run_results(test_api: API) -> list[SetupActionResult]:
        """Dry-run the bootstrap manifest and return all action results."""
        setup_params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)
        preview = test_api.sync.preview_batch(setup_params)
        results = execute_via_stream(test_api, preview, setup_params)

        assert len(results.manifest_results) == 1
        return results.manifest_results[0].results

    @staticmethod
    def test_installable_actions_skipped(dry_run_results: list[SetupActionResult]) -> None:
        """Runtime, package, and tool actions should be skipped when already installed."""
        installable = [
            r for r in dry_run_results if r.action.kind in {PluginKind.RUNTIME, PluginKind.PACKAGE, PluginKind.TOOL}
        ]
        assert len(installable) > 0, 'No installable actions produced by dry-run'
        not_skipped = [r for r in installable if not r.skipped]
        assert not not_skipped, f'{len(not_skipped)} installable action(s) not skipped: ' + ', '.join(
            r.action.description for r in not_skipped
        )

    @staticmethod
    def test_scm_action_present(dry_run_results: list[SetupActionResult]) -> None:
        """An SCM_CLONE action should be present in the dry-run results."""
        scm_results = [r for r in dry_run_results if r.action.kind == PluginKind.SCM]
        assert len(scm_results) == 1

    @staticmethod
    def test_command_present(dry_run_results: list[SetupActionResult]) -> None:
        """The post-sync RUN_COMMAND should be present in the dry-run results."""
        command_results = [r for r in dry_run_results if r.action.command is not None]
        assert len(command_results) == 1
