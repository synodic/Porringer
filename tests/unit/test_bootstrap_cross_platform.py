"""Cross-platform tests for the python-bootstrap example manifest.

Validates that deferred resolution works correctly when platform-
specific plugins are unavailable:

- ``pim`` requires the Windows ``py`` launcher
- ``pyenv`` requires the Unix ``pyenv`` CLI

When neither is available, a RUNTIME action should still appear in
the preview with ``installer=None`` (deferred), rather than being
silently dropped.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from porringer.backend.command.sync import SyncCommands
from porringer.core.schema import PluginKind
from porringer.schema import SetupResults

_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


class TestBootstrapDeferredRuntime:
    """Verify preview behaviour when no runtime provider is available."""

    @staticmethod
    @pytest.fixture
    def preview_no_runtime() -> SetupResults:
        """Preview the bootstrap manifest with both py and pyenv unavailable."""
        original_which = __import__('shutil').which

        def _which_no_runtime(cmd: str) -> str | None:
            if cmd in {'py', 'pyenv'}:
                return None
            return original_which(cmd)

        with patch('shutil.which', side_effect=_which_no_runtime):
            return SyncCommands.preview_single(_BOOTSTRAP_DIR)

    @staticmethod
    def test_manifest_loads(preview_no_runtime: SetupResults) -> None:
        """The manifest still loads even without a runtime provider."""
        assert preview_no_runtime is not None
        assert len(preview_no_runtime.actions) > 0

    @staticmethod
    def test_runtime_action_deferred(preview_no_runtime: SetupResults) -> None:
        """A RUNTIME action is generated with installer=None (deferred).

        Previously, the engine silently dropped the section when no
        runtime provider was available.  Now it defers, matching the
        existing TOOL behaviour.
        """
        runtime_actions = [a for a in preview_no_runtime.actions if a.kind == PluginKind.RUNTIME]
        assert len(runtime_actions) == 1
        assert runtime_actions[0].installer is None, 'Expected deferred (installer=None)'
        assert runtime_actions[0].package is not None
        assert runtime_actions[0].package.name == '3.14'

    @staticmethod
    def test_all_phases_present(preview_no_runtime: SetupResults) -> None:
        """All phases (runtime, package, tool, scm, command) appear in the preview."""
        kinds = {a.kind for a in preview_no_runtime.actions}
        assert PluginKind.RUNTIME in kinds
        assert PluginKind.PACKAGE in kinds
        assert PluginKind.TOOL in kinds
        assert PluginKind.SCM in kinds
        assert None in kinds

    @staticmethod
    def test_deferred_description(preview_no_runtime: SetupResults) -> None:
        """Deferred actions should have '(deferred)' in their description."""
        runtime_actions = [a for a in preview_no_runtime.actions if a.kind == PluginKind.RUNTIME]
        assert 'deferred' in runtime_actions[0].description.lower()
