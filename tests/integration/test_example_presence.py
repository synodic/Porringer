"""Presence test using the python-dev example manifest.

Verifies that pip packages declared in `examples/python-dev/porringer.json`
which also appear in `pyproject.toml` dependency groups are detected as
already installed when dry-running inside the development environment.
"""

from pathlib import Path

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import SetupParameters

# Absolute path to the example manifest directory
_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-dev'

# Packages present in both the example manifest and pyproject.toml dependency
# groups (lint + test).  These MUST be installed in the dev venv.
_EXPECTED_PRESENT = {'pytest', 'pytest-cov', 'pytest-mock', 'pytest-asyncio', 'ruff', 'pyrefly'}


def test_dev_deps_detected_as_present(test_api: API) -> None:
    """Dry-run the python-dev manifest and verify every dev dep is skipped as already installed."""
    results = test_api.sync.run(SetupParameters(paths=_EXAMPLE_DIR, dry_run=True))

    assert len(results.manifest_results) == 1
    mr = results.manifest_results[0]

    result_map = {
        r.action.package.name: r.skipped
        for r in mr.results
        if r.action.kind == PluginKind.PACKAGE and r.action.installer == 'pip' and r.action.package is not None
    }

    missing = _EXPECTED_PRESENT - result_map.keys()
    not_skipped = {pkg for pkg in _EXPECTED_PRESENT if pkg in result_map and not result_map[pkg]}

    assert not missing, f'Expected packages not in dry-run results: {missing}'
    assert not not_skipped, f'Expected packages not detected as installed: {not_skipped}'
