"""Helpers for test example presence."""

"""Presence test using the python-dev example manifest.

Verifies that pip packages declared in `examples/python-dev/porringer.json`
which also appear in `pyproject.toml` dependency groups are detected as
already installed when inspected inside the development environment.
"""

import sys
from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PluginKind
from porringer.schema import InspectionStatus, SetupParameters

# Absolute path to the example manifest directory
_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-dev'

# Packages present in both the example manifest and pyproject.toml dependency
# groups (lint + test).  These MUST be installed in the dev venv.
_EXPECTED_PRESENT = {'pytest', 'pytest-asyncio', 'ruff', 'pyrefly'}


@pytest.mark.fresh_plugins
async def test_dev_deps_detected_as_present(test_api: API) -> None:
    """Inspect the python-dev manifest and verify every dev dep is satisfied."""
    # Pin presence detection to the interpreter running the tests (this venv,
    # which has the dev deps installed).  Without an explicit runtime context,
    # inspection resolves ``python`` from RuntimeProvider plugins / PATH, which
    # may select a different interpreter and make the result order-dependent.
    plugins = await API.discover_plugins(resolve_runtime=False)
    plugins.runtime_context = RuntimeContext(executables={'python': Path(sys.executable)})
    report = await test_api.sync.inspect(SetupParameters(paths=_EXAMPLE_DIR), plugins=plugins)

    assert len(report.manifests) == 1
    mr = report.manifests[0]

    result_map = {
        r.action.package_name: r.status
        for r in mr.actions
        if r.action.kind == PluginKind.PACKAGE.value
        and r.action.installer == 'pip'
        and r.action.package_name is not None
    }

    missing = _EXPECTED_PRESENT - result_map.keys()
    present_statuses = {InspectionStatus.SATISFIED, InspectionStatus.UPDATE_AVAILABLE}
    not_present = {pkg for pkg in _EXPECTED_PRESENT if result_map.get(pkg) not in present_statuses}

    assert not missing, f'Expected packages not in inspection results: {missing}'
    assert not not_present, f'Expected packages not detected as installed: {not_present}'
