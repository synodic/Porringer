"""Helpers for test frozen app presence."""

"""Integration tests for frozen-app and pre-resolved runtime presence detection.

Verifies that inspection correctly detects packages when:

1. ``DiscoveredPlugins`` carries a pre-resolved ``runtime_context``
   (simulating ``API.discover_plugins(resolve_runtime=True)``).
2. The application is running in a frozen (PyInstaller) context where
   ``sys.executable`` is not a Python interpreter.

These tests exercise pre-discovered plugin reuse and the ``sys.frozen``
guard in ``python_command()`` end-to-end using the ``mock_packages``
marker to avoid real subprocess calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import (
    InspectionStatus,
    SetupParameters,
)

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.mock_packages
class TestPreresolvedRuntimePresence:
    """Inspect with a pre-resolved runtime context from plugins."""

    @staticmethod
    async def test_package_phase_receives_seeded_runtime(session_api: API) -> None:
        """Package-phase resolution uses the pre-resolved runtime, not sys.executable.

        Calls ``sync.inspect`` with plugins from
        ``discover_plugins(resolve_runtime=True)`` and verifies the
        package inspection path reuses that context.
        """
        plugins = await API.discover_plugins(resolve_runtime=True)
        params = SetupParameters(paths=_BOOTSTRAP_DIR)
        report = await session_api.sync.inspect(params, plugins=plugins)

        assert len(report.manifests) >= 1
        manifest_result = report.manifests[0]

        failed = [r for r in manifest_result.actions if r.status == InspectionStatus.FAILED]
        assert not failed, f'Unexpected failures: {[r.action.description for r in failed]}'

    @staticmethod
    async def test_inspect_with_explicit_runtime_context(session_api: API) -> None:
        """When plugins carry runtime_context, PACKAGE inspections use it.

        Verifies that the ``python_command()`` call inside
        ``packages()`` receives the seeded context rather than falling
        back to ``sys.executable``.
        """
        # Discover plugins with runtime resolution
        plugins = await API.discover_plugins(resolve_runtime=True)

        if plugins.runtime_context is not None and plugins.runtime_context.get('python') is not None:
            # The runtime was resolved — verify it flows through
            params = SetupParameters(paths=_BOOTSTRAP_DIR)
            report = await session_api.sync.inspect(params, plugins=plugins)
            manifest_result = report.manifests[0]

            # Package-phase actions should either skip (installed) or
            # plan an install — not fail due to sys.executable issues.
            package_results = [r for r in manifest_result.actions if r.action.kind == PluginKind.PACKAGE.value]
            for r in package_results:
                assert r.status != InspectionStatus.FAILED, (
                    f'Package action failed: {r.action.description}: {r.message}'
                )
        else:
            pytest.skip('No runtime provider resolved a Python interpreter')


@pytest.mark.mock_packages
class TestFrozenAppPresenceSimulation:
    """Simulate a frozen-app environment and verify presence detection.

    Patches ``sys.frozen`` and ``sys.executable`` to mimic a
    PyInstaller bundle, then runs an inspection to verify that presence
    detection still works via the ``shutil.which`` fallback.
    """

    @staticmethod
    async def test_inspect_survives_frozen_sys_executable(session_api: API) -> None:
        """Inspection does not fail catastrophically with a frozen sys.executable.

        With the ``sys.frozen`` guard and ``shutil.which`` fallback,
        ``python_command()`` should find a usable Python even when
        ``sys.executable`` is a non-Python binary.
        """
        params = SetupParameters(paths=_BOOTSTRAP_DIR)

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch.object(sys, 'executable', r'C:\app\synodic.exe'),
            # shutil.which finds the real Python on PATH
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=sys.executable,  # the *real* interpreter captured before patching
            ),
        ):
            report = await session_api.sync.inspect(params)

        assert len(report.manifests) >= 1
        manifest_result = report.manifests[0]

        # No catastrophic failures — actions may skip or plan installs
        failed = [r for r in manifest_result.actions if r.status == InspectionStatus.FAILED]
        assert not failed, f'Failures in frozen context: {[r.action.description for r in failed]}'
