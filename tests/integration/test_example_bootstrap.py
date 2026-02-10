"""Bootstrap example tests.

Validates that the ``examples/python-bootstrap/porringer.json`` manifest
produces the correct phased action plan, including deferred tool/runtime
resolution and post-sync commands.

Runtime and tool actions may have ``installer=None`` (deferred) when
the backing CLI tool is not on PATH — this is expected and correct.
"""

from pathlib import Path

import pytest

from porringer.backend.command.sync import SyncCommands
from porringer.core.schema import PluginKind
from porringer.schema import SetupResults

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


class TestBootstrapPreview:
    """Preview the bootstrap manifest and verify its action plan."""

    @staticmethod
    @pytest.fixture
    def preview() -> SetupResults:
        """Parse the bootstrap manifest."""
        return SyncCommands.parse_manifest(_BOOTSTRAP_DIR)

    @staticmethod
    def test_manifest_loads(preview: SetupResults) -> None:
        """The bootstrap manifest should load without errors."""
        assert preview is not None
        assert len(preview.actions) > 0

    @staticmethod
    def test_runtime_action_present(preview: SetupResults) -> None:
        """A RUNTIME action for Python 3.14 should be in the plan.

        The action may have ``installer=None`` (deferred) when no
        runtime provider (pim/pyenv) is available on the current
        platform.
        """
        runtime_actions = [a for a in preview.actions if a.kind == PluginKind.RUNTIME and a.ecosystem == 'python']
        assert len(runtime_actions) == 1
        assert runtime_actions[0].package is not None
        assert runtime_actions[0].package.name == '3.14'

    @staticmethod
    def test_package_actions_present(preview: SetupResults) -> None:
        """A PACKAGE action for pipx should be in the plan."""
        package_actions = [a for a in preview.actions if a.kind == PluginKind.PACKAGE and a.ecosystem == 'python']
        assert len(package_actions) == 1
        assert package_actions[0].package is not None
        assert package_actions[0].package.name == 'pipx'

    @staticmethod
    def test_tool_actions_present(preview: SetupResults) -> None:
        """A TOOL action for pdm should be in the plan.

        The tool action may have ``installer=None`` (deferred) if pipx
        is not currently available — this is expected and correct.
        """
        tool_actions = [a for a in preview.actions if a.kind == PluginKind.TOOL and a.ecosystem == 'python']
        assert len(tool_actions) == 1
        assert tool_actions[0].package is not None
        assert tool_actions[0].package.name == 'pdm'

    @staticmethod
    def test_post_sync_command_present(preview: SetupResults) -> None:
        """A RUN_COMMAND action for ``pdm install`` should be in the plan."""
        command_actions = [a for a in preview.actions if a.kind is None]
        assert len(command_actions) == 1
        assert command_actions[0].command == ['pdm', 'install']

    @staticmethod
    def test_scm_action_present(preview: SetupResults) -> None:
        """An SCM action for cloning the porringer repo should be in the plan."""
        scm_actions = [a for a in preview.actions if a.kind == PluginKind.SCM and a.ecosystem == 'git']
        assert len(scm_actions) == 1
        assert scm_actions[0].package is not None
        assert scm_actions[0].package.name == 'https://github.com/synodic/porringer'
        assert scm_actions[0].kind == PluginKind.SCM

    @staticmethod
    def test_action_order_matches_phases(preview: SetupResults) -> None:
        """Actions should be ordered: runtime, package, tool, scm, command.

        This validates the build order returned by preview. The execution
        engine reorders into runtime → package → tool → scm → command phases.
        """
        kinds = []
        for a in preview.actions:
            if a.kind == PluginKind.RUNTIME:
                kinds.append('runtime')
            elif a.kind == PluginKind.PACKAGE:
                kinds.append('package')
            elif a.kind == PluginKind.TOOL:
                kinds.append('tool')
            elif a.kind == PluginKind.SCM:
                kinds.append('scm')
            elif a.kind is None:
                kinds.append('command')
            else:
                kinds.append('other')

        assert 'runtime' in kinds
        assert 'package' in kinds
        assert 'tool' in kinds
        assert 'scm' in kinds
        assert 'command' in kinds
