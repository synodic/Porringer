"""Helpers for test dry run acceptance.

Real-tool dry-run acceptance smoke tests.

These verify that the *real* wrapped tool accepts the argv a plugin
generates when run in the tool's native dry-run mode.  Porringer's own
``dry_run=True`` only resolves the operation and never invokes the tool,
so this is the layer that catches a tool renaming or dropping a flag the
plugin still emits — it fails loudly instead of passing a stale golden
string.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.plugin_schema.environment import PackageVerb
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PackageRef
from tests.fixtures.disposable_environment import DisposableToolEnvironmentFactory
from tests.fixtures.smoke_packages import PythonSmokePackage
from tests.fixtures.tool_smoke import assert_native_dry_run_accepted


def _skip_if_interpreter_lacks(module: str) -> None:
    """Skip when the current interpreter cannot import *module*."""
    result = subprocess.run(
        [sys.executable, '-c', f'import {module}'],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f'{module!r} is not importable in {sys.executable}')


@pytest.mark.fresh_plugins
@pytest.mark.parametrize('verb', ['install', 'upgrade'])
async def test_pip_dry_run_is_accepted(
    disposable_tool_environment_factory: DisposableToolEnvironmentFactory,
    python_smoke_package: PythonSmokePackage,
    monkeypatch: pytest.MonkeyPatch,
    verb: PackageVerb,
) -> None:
    """Real pip accepts the argv the pip plugin builds for ``--dry-run``.

    Resolution is forced offline against the local smoke wheel, so the
    rehearsal touches no network and installs nothing.
    """
    _skip_if_interpreter_lacks('pip')
    disposable_tool_environment_factory()
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', str(python_smoke_package.wheel.parent))
    monkeypatch.setenv('PIP_DISABLE_PIP_VERSION_CHECK', '1')

    plugins = await API.discover_plugins(use_cache=False, resolve_runtime=False)
    runtime_context = RuntimeContext(executables={'python': Path(sys.executable)})
    plugin = plugins.environments['pip']
    ref = PackageRef.model_validate(python_smoke_package.requirement)

    await assert_native_dry_run_accepted(plugin, ref, verb=verb, runtime_context=runtime_context)
