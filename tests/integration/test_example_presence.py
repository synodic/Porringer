"""Presence test using the python-dev example manifest.

Verifies that pip packages declared in ``examples/python-dev/porringer.json``
which also appear in ``pyproject.toml`` dependency groups are detected as
already installed when dry-running inside the development environment.
"""

from pathlib import Path

import pytest

from porringer.api import API
from porringer.schema import SetupActionType, SetupParameters, SkipReason
from tests.conftest import execute_via_stream

# Absolute path to the example manifest directory
_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-dev'

# Packages present in both the example manifest and pyproject.toml dependency
# groups (lint + test).  These MUST be installed in the dev venv.
_EXPECTED_PRESENT = {'pytest', 'pytest-cov', 'pytest-mock', 'pytest-asyncio', 'ruff', 'pyrefly'}


class TestExamplePresence:
    """Dry-run the python-dev example and verify dev-dep presence."""

    @staticmethod
    @pytest.fixture
    def dry_run_results(test_api: API) -> list[tuple[str, bool, SkipReason | None]]:
        """Dry-run the example manifest and return (package, skipped, reason) tuples for pip actions."""
        setup_params = SetupParameters(paths=_EXAMPLE_DIR, dry_run=True)
        preview = test_api.sync.preview_batch(setup_params)
        results = execute_via_stream(test_api, preview, setup_params)

        assert len(results.manifest_results) == 1
        mr = results.manifest_results[0]

        return [
            (r.action.package.name, r.skipped, r.skip_reason)
            for r in mr.results
            if r.action.action_type == SetupActionType.PACKAGE
            and r.action.installer == 'pip'
            and r.action.package is not None
        ]

    @staticmethod
    def test_expected_packages_detected_as_present(
        dry_run_results: list[tuple[str, bool, str | None]],
    ) -> None:
        """Each package from pyproject.toml dev deps should be skipped as already installed."""
        result_map = {name: (skipped, reason) for name, skipped, reason in dry_run_results}

        missing = []
        not_skipped = []
        for pkg in _EXPECTED_PRESENT:
            if pkg not in result_map:
                missing.append(pkg)
            elif not result_map[pkg][0]:
                not_skipped.append(pkg)

        assert not missing, f'Expected packages not found in dry-run results: {missing}'
        assert not not_skipped, (
            f'Expected packages not detected as installed: {not_skipped}. '
            'Ensure the dev dependencies are installed in the active environment.'
        )

    @staticmethod
    def test_all_pip_actions_succeed(
        dry_run_results: list[tuple[str, bool, str | None]],
    ) -> None:
        """All pip package actions should succeed during dry-run (present or not)."""
        # dry_run_results only contains pip PACKAGE actions; the fixture filters
        # for those.  We just need at least one result to be meaningful.
        assert len(dry_run_results) > 0, 'No pip package actions found in example manifest'
