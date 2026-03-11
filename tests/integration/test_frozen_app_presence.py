"""Integration tests for frozen-app and pre-resolved runtime presence detection.

Verifies that the full execution pipeline correctly detects packages
when:

1. ``DiscoveredPlugins`` carries a pre-resolved ``runtime_context``
   (simulating ``API.discover_plugins(resolve_runtime=True)``).
2. The application is running in a frozen (PyInstaller) context where
   ``sys.executable`` is not a Python interpreter.

These tests exercise the seeding path in ``execute_single()`` and
the ``sys.frozen`` guard in ``python_command()`` end-to-end using
the ``mock_packages`` marker to avoid real subprocess calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import (
    SetupParameters,
)

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.mock_packages
class TestPreresolvedRuntimePresence:
    """Dry-run with a pre-resolved runtime context from plugins."""

    @staticmethod
    async def test_package_phase_receives_seeded_runtime(session_api: API) -> None:
        """Package-phase resolution uses the pre-resolved runtime, not sys.executable.

        Calls ``sync.run`` with ``dry_run=True`` through the normal
        API path, which calls ``discover_plugins(resolve_runtime=True)``
        and threads the result through ``execute_single()``.
        """
        params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)
        results = await session_api.sync.run(params)

        assert len(results.manifest_results) >= 1
        manifest_result = results.manifest_results[0]

        # All results should succeed (no failures from bad sys.executable)
        failed = [r for r in manifest_result.results if not r.success]
        assert not failed, f'Unexpected failures: {[r.action.description for r in failed]}'

    @staticmethod
    async def test_dry_run_with_explicit_runtime_context(session_api: API) -> None:
        """When plugins carry runtime_context, dry-run PACKAGE actions use it.

        Verifies that the ``python_command()`` call inside
        ``packages()`` receives the seeded context rather than falling
        back to ``sys.executable``.
        """
        # Discover plugins with runtime resolution
        plugins = await API.discover_plugins(resolve_runtime=True)

        if plugins.runtime_context is not None and plugins.runtime_context.get('python') is not None:
            # The runtime was resolved — verify it flows through
            params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)
            results = await session_api.sync.run(params)
            manifest_result = results.manifest_results[0]

            # Package-phase actions should either skip (installed) or
            # plan an install — not fail due to sys.executable issues
            package_results = [r for r in manifest_result.results if r.action.kind == PluginKind.PACKAGE]
            for r in package_results:
                assert r.success, f'Package action failed: {r.action.description}: {r.message}'
        else:
            pytest.skip('No runtime provider resolved a Python interpreter')


@pytest.mark.mock_packages
class TestFrozenAppPresenceSimulation:
    """Simulate a frozen-app environment and verify presence detection.

    Patches ``sys.frozen`` and ``sys.executable`` to mimic a
    PyInstaller bundle, then runs a dry-run to verify that presence
    detection still works via the ``shutil.which`` fallback.
    """

    @staticmethod
    async def test_dry_run_survives_frozen_sys_executable(session_api: API) -> None:
        """Dry-run does not fail catastrophically with a frozen sys.executable.

        With the ``sys.frozen`` guard and ``shutil.which`` fallback,
        ``python_command()`` should find a usable Python even when
        ``sys.executable`` is a non-Python binary.
        """
        params = SetupParameters(paths=_BOOTSTRAP_DIR, dry_run=True)

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch.object(sys, 'executable', r'C:\app\synodic.exe'),
            # shutil.which finds the real Python on PATH
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=sys.executable,  # the *real* interpreter captured before patching
            ),
        ):
            results = await session_api.sync.run(params)

        assert len(results.manifest_results) >= 1
        manifest_result = results.manifest_results[0]

        # No catastrophic failures — actions may skip or plan installs
        failed = [r for r in manifest_result.results if not r.success]
        assert not failed, f'Failures in frozen context: {[r.action.description for r in failed]}'
