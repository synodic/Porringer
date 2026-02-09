"""Presence test using the python-bootstrap example manifest.

Verifies that when runtimes, packages, and tools declared in
``examples/python-bootstrap/porringer.json`` are already installed,
a dry-run reports every action as skipped — including the post-sync
``RUN_COMMAND`` which is skipped because nothing changed.
"""

from pathlib import Path

import pytest

from porringer.api import API
from porringer.schema import SetupActionResult, SetupParameters, SkipReason
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
    def test_all_actions_skipped(dry_run_results: list[SetupActionResult]) -> None:
        """Every action should be skipped when prerequisites are present."""
        assert len(dry_run_results) > 0, 'No actions produced by dry-run'
        not_skipped = [r for r in dry_run_results if not r.skipped]
        assert not not_skipped, f'{len(not_skipped)} action(s) not skipped: ' + ', '.join(
            r.action.description for r in not_skipped
        )

    @staticmethod
    def test_command_skipped_as_nothing_changed(dry_run_results: list[SetupActionResult]) -> None:
        """The post-sync RUN_COMMAND should be skipped with NOTHING_CHANGED reason."""
        command_results = [r for r in dry_run_results if r.action.command is not None]
        assert len(command_results) == 1
        assert command_results[0].skipped
        assert command_results[0].skip_reason == SkipReason.NOTHING_CHANGED
