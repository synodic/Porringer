"""Presence test using the python-bootstrap example manifest.

Verifies that the dry-run presence check works correctly for the
`examples/python-bootstrap/porringer.json` manifest.

Actions whose backing installer is available and whose package is
already installed should be skipped.  Actions with deferred
installers (`installer=None` — no provider on PATH) pass through
dry-run as not-skipped because presence cannot be checked.
"""

import importlib.metadata
from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import SetupActionResult, SetupParameters, SkipReason

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.fresh_plugins
class TestBootstrapPresence:
    """Dry-run the python-bootstrap example and verify presence detection."""

    @staticmethod
    @pytest.fixture(scope='class')
    async def dry_run_results(session_api: API) -> list[SetupActionResult]:
        """Dry-run the bootstrap manifest and return all action results.

        Class-scoped: the dry-run is executed once and shared across
        every test in this class (all tests are read-only).
        """
        setup_params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)
        results = await session_api.sync.run(setup_params)

        assert len(results.manifest_results) == 1
        return results.manifest_results[0].results

    @staticmethod
    def test_all_manifest_sections_produce_results(dry_run_results: list[SetupActionResult]) -> None:
        """Every manifest section (runtime, package, tool, scm, command) yields at least one result."""
        kinds = {r.action.kind for r in dry_run_results}
        assert PluginKind.RUNTIME in kinds, 'No RUNTIME result'
        assert PluginKind.PACKAGE in kinds, 'No PACKAGE result'
        assert PluginKind.TOOL in kinds, 'No TOOL result'
        assert PluginKind.SCM in kinds, 'No SCM result'
        assert None in kinds, 'No post-sync command result'

    @staticmethod
    def test_dry_run_actions_succeed(dry_run_results: list[SetupActionResult]) -> None:
        """All dry-run actions should complete successfully.

        Actions with `installer=None` (deferred) succeed as no-ops.
        Actions with a resolved installer succeed by either skipping
        (already-installed) or reporting they would install.
        """
        failed = [r for r in dry_run_results if not r.success]
        assert not failed, f'{len(failed)} action(s) failed: ' + ', '.join(
            f'{r.action.description}: {r.message}' for r in failed
        )

    @staticmethod
    def test_skipped_actions_have_reason(dry_run_results: list[SetupActionResult]) -> None:
        """Every skipped action should carry a valid skip reason."""
        skipped = [r for r in dry_run_results if r.skipped]
        for r in skipped:
            assert r.skip_reason is not None, f'Skipped action without reason: {r.action.description}'

    @staticmethod
    def test_already_installed_skip_reason(dry_run_results: list[SetupActionResult]) -> None:
        """Installable actions detected as present should report ALREADY_INSTALLED."""
        skipped = [
            r
            for r in dry_run_results
            if r.skipped and r.action.kind in {PluginKind.RUNTIME, PluginKind.PACKAGE, PluginKind.TOOL}
        ]
        for r in skipped:
            assert r.skip_reason == SkipReason.ALREADY_INSTALLED, (
                f'{r.action.description} skipped with unexpected reason: {r.skip_reason}'
            )

    @staticmethod
    def test_pipx_skipped_when_installed(dry_run_results: list[SetupActionResult]) -> None:
        """If `pipx` is installed as a pip package, its PACKAGE action should be skipped.

        The presence check uses `pip list` (not PATH), so we guard
        with `importlib.metadata` which matches pip's view.
        """
        try:
            importlib.metadata.distribution('pipx')
        except importlib.metadata.PackageNotFoundError:
            pytest.skip('pipx not installed as a pip package')
        pipx_results = [
            r
            for r in dry_run_results
            if r.action.kind == PluginKind.PACKAGE and r.action.package and r.action.package.name == 'pipx'
        ]
        assert len(pipx_results) == 1
        assert pipx_results[0].skipped, 'pipx is installed but action was not skipped'

    @staticmethod
    def test_scm_action_present(dry_run_results: list[SetupActionResult]) -> None:
        """An SCM action should be present in the dry-run results."""
        scm_results = [r for r in dry_run_results if r.action.kind == PluginKind.SCM]
        assert len(scm_results) == 1

    @staticmethod
    def test_command_present(dry_run_results: list[SetupActionResult]) -> None:
        """The post-sync command should be present in the dry-run results."""
        command_results = [r for r in dry_run_results if r.action.command is not None]
        assert len(command_results) == 1
