"""Tests for WSL2 transport, manifest sections, and action builder integration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from porringer.backend.command.core.action_builder import build_actions
from porringer.backend.command.core.execution import _overlay_wsl_plugin, _wsl_transport_for
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters
from porringer.core.transport import LocalTransport, Transport
from porringer.plugin.wsl.transport import WslTransport
from porringer.plugin.wsl.utility import is_inside_wsl, is_wsl_host, native_distro
from porringer.schema import PackageSpec, SetupManifest, SyncStrategy
from porringer.schema.manifest import WslDistroManifest

from tests.fixtures.mock_plugins import MOCK_DIST, MockPythonEnv, MockRuntimeProvider

_PY = Ecosystem('python')
_SYS = Ecosystem('system')


# ---------------------------------------------------------------------------
# Transport unit tests
# ---------------------------------------------------------------------------


class TestLocalTransport:
    """LocalTransport is a passthrough."""

    @staticmethod
    def test_transform_args_identity() -> None:
        t = LocalTransport()
        args = ['pip', 'install', 'requests']
        assert t.transform_args(args) is args

    @staticmethod
    def test_transform_cwd_identity() -> None:
        t = LocalTransport()
        cwd = Path('/some/path')
        assert t.transform_cwd(cwd) is cwd

    @staticmethod
    def test_transform_cwd_none() -> None:
        t = LocalTransport()
        assert t.transform_cwd(None) is None

    @staticmethod
    def test_check_tool_delegates_to_which() -> None:
        t = LocalTransport()
        with patch('porringer.core.transport.shutil.which', return_value='/usr/bin/git'):
            assert t.check_tool('git') is True
        with patch('porringer.core.transport.shutil.which', return_value=None):
            assert t.check_tool('nonexistent') is False

    @staticmethod
    def test_is_transport_protocol() -> None:
        assert isinstance(LocalTransport(), Transport)


class TestWslTransport:
    """WslTransport prepends wsl --exec -d <distro>."""

    @staticmethod
    def test_transform_args_prepends_wsl() -> None:
        t = WslTransport('Ubuntu-22.04')
        result = t.transform_args(['apt', 'install', 'curl'])
        assert result == ['wsl', '--exec', '-d', 'Ubuntu-22.04', 'apt', 'install', 'curl']

    @staticmethod
    def test_transform_args_empty() -> None:
        t = WslTransport('Debian')
        result = t.transform_args([])
        assert result == ['wsl', '--exec', '-d', 'Debian']

    @staticmethod
    def test_distro_property() -> None:
        t = WslTransport('Ubuntu-22.04')
        assert t.distro == 'Ubuntu-22.04'

    @staticmethod
    def test_transform_cwd_none() -> None:
        t = WslTransport('Ubuntu')
        assert t.transform_cwd(None) is None

    @staticmethod
    def test_transform_cwd_posix_passthrough() -> None:
        t = WslTransport('Ubuntu')
        cwd = Path('/home/user/project')
        result = t.transform_cwd(cwd)
        assert result == cwd

    @staticmethod
    def test_transform_cwd_windows_path() -> None:
        """Windows drive paths should be translated via windows_to_wsl_path."""
        t = WslTransport('Ubuntu')
        with patch('porringer.plugin.wsl.transport.windows_to_wsl_path', return_value='/mnt/c/Users/me'):
            result = t.transform_cwd(Path('C:\\Users\\me'))
            assert result == Path('/mnt/c/Users/me')

    @staticmethod
    def test_transform_cwd_windows_path_failure() -> None:
        """When translation fails, original path is returned."""
        t = WslTransport('Ubuntu')
        original = Path('C:\\Users\\me')
        with patch('porringer.plugin.wsl.transport.windows_to_wsl_path', return_value=None):
            result = t.transform_cwd(original)
            assert result == original

    @staticmethod
    def test_check_tool_delegates_to_wsl_which() -> None:
        t = WslTransport('Ubuntu')
        with patch('porringer.plugin.wsl.transport.wsl_which', return_value=True) as mock_which:
            assert t.check_tool('git') is True
            mock_which.assert_called_once_with('Ubuntu', 'git')

    @staticmethod
    def test_is_transport_protocol() -> None:
        assert isinstance(WslTransport('test'), Transport)

    @staticmethod
    def test_repr() -> None:
        t = WslTransport('Ubuntu-22.04')
        assert 'Ubuntu-22.04' in repr(t)


# ---------------------------------------------------------------------------
# Plugin.with_transport
# ---------------------------------------------------------------------------


class TestWithTransport:
    """Plugin.with_transport() creates a new instance with a different transport."""

    @staticmethod
    def test_new_instance_different_transport() -> None:
        env = MockRuntimeProvider(MOCK_DIST)
        assert isinstance(env._transport, LocalTransport)

        wsl = WslTransport('Ubuntu')
        new_env = env.with_transport(wsl)

        assert new_env is not env
        assert isinstance(new_env, MockRuntimeProvider)
        assert new_env._transport is wsl

    @staticmethod
    def test_preserves_distribution() -> None:
        env = MockRuntimeProvider(MOCK_DIST)
        new_env = env.with_transport(WslTransport('Debian'))
        assert new_env._distribution == env._distribution

    @staticmethod
    def test_original_unchanged() -> None:
        env = MockRuntimeProvider(MOCK_DIST)
        env.with_transport(WslTransport('Debian'))
        assert isinstance(env._transport, LocalTransport)


# ---------------------------------------------------------------------------
# WSL detection helpers
# ---------------------------------------------------------------------------


class TestWslDetection:
    """Unit tests for WSL detection utilities."""

    @staticmethod
    def test_is_wsl_host_on_windows() -> None:
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.shutil.which', return_value='C:\\Windows\\system32\\wsl.exe'),
        ):
            mock_sys.platform = 'win32'
            assert is_wsl_host() is True

    @staticmethod
    def test_is_wsl_host_not_windows() -> None:
        with patch('porringer.plugin.wsl.utility.sys') as mock_sys:
            mock_sys.platform = 'linux'
            assert is_wsl_host() is False

    @staticmethod
    def test_is_wsl_host_no_wsl_exe() -> None:
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.shutil.which', return_value=None),
        ):
            mock_sys.platform = 'win32'
            assert is_wsl_host() is False

    @staticmethod
    def test_is_inside_wsl_linux_with_microsoft() -> None:
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.Path') as mock_path,
        ):
            mock_sys.platform = 'linux'
            mock_path.return_value.read_text.return_value = (
                'Linux version 5.15.153.1-microsoft-standard-WSL2'
            )
            assert is_inside_wsl() is True

    @staticmethod
    def test_is_inside_wsl_not_linux() -> None:
        with patch('porringer.plugin.wsl.utility.sys') as mock_sys:
            mock_sys.platform = 'win32'
            assert is_inside_wsl() is False


# ---------------------------------------------------------------------------
# WslDistroManifest schema
# ---------------------------------------------------------------------------


class TestWslDistroManifest:
    """Schema validation for WSL distro manifest sections."""

    @staticmethod
    def test_empty_manifest() -> None:
        m = WslDistroManifest()
        assert m.packages == {}
        assert m.tools == {}
        assert m.projects == {}
        assert m.runtimes == {}
        assert m.scm == {}
        assert m.preferences == {}

    @staticmethod
    def test_packages_section() -> None:
        m = WslDistroManifest.model_validate({
            'packages': {'system': ['curl', 'build-essential']},
        })
        assert len(m.packages[Ecosystem('system')]) == 2

    @staticmethod
    def test_preferences_override() -> None:
        m = WslDistroManifest.model_validate({
            'packages': {'python': ['requests']},
            'preferences': {'python': 'pip'},
        })
        assert m.preferences[Ecosystem('python')] == 'pip'


class TestSetupManifestWsl2:
    """SetupManifest.wsl2 field integration."""

    @staticmethod
    def test_wsl2_defaults_to_empty() -> None:
        m = SetupManifest()
        assert m.wsl2 == {}

    @staticmethod
    def test_wsl2_single_distro() -> None:
        m = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu-22.04': {
                    'packages': {'system': ['curl']},
                },
            },
        })
        assert 'Ubuntu-22.04' in m.wsl2
        distro = m.wsl2['Ubuntu-22.04']
        assert len(distro.packages[Ecosystem('system')]) == 1

    @staticmethod
    def test_wsl2_multiple_distros() -> None:
        m = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu-22.04': {'packages': {'system': ['curl']}},
                'Debian': {'packages': {'system': ['wget']}},
            },
        })
        assert len(m.wsl2) == 2

    @staticmethod
    def test_iter_wsl_sections() -> None:
        m = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {
                    'packages': {'system': ['curl']},
                    'runtimes': {'python': ['3.12']},
                },
            },
        })
        sections = list(m.iter_wsl_sections())
        assert len(sections) == 2

        # Each entry is (distro, kind, ecosystem, packages)
        distros = {s[0] for s in sections}
        assert distros == {'Ubuntu'}

        kinds = {s[1] for s in sections}
        assert PluginKind.PACKAGE in kinds
        assert PluginKind.RUNTIME in kinds

    @staticmethod
    def test_iter_wsl_sections_empty_when_no_wsl2() -> None:
        m = SetupManifest(packages={_PY: [PackageSpec(name='requests')]})
        assert list(m.iter_wsl_sections()) == []


# ---------------------------------------------------------------------------
# build_actions WSL integration
# ---------------------------------------------------------------------------


class TestBuildActionsWsl:
    """build_actions produces WSL-tagged actions from wsl2 manifest sections."""

    @staticmethod
    def test_wsl_actions_have_distro_set() -> None:
        """WSL package actions carry the distro name."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {
                    'packages': {'python': ['requests', 'flask']},
                },
            },
        })
        env = MockPythonEnv(MOCK_DIST)
        environments = {'mock-pip': env}
        actions = build_actions(manifest, environments)

        wsl_actions = [a for a in actions if a.distro is not None]
        assert len(wsl_actions) == 2
        assert all(a.distro == 'Ubuntu' for a in wsl_actions)
        assert all(a.kind == PluginKind.PACKAGE for a in wsl_actions)

    @staticmethod
    def test_wsl_actions_description_prefix() -> None:
        """WSL action descriptions start with [WSL:<distro>]."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Debian': {
                    'packages': {'python': ['requests']},
                },
            },
        })
        env = MockPythonEnv(MOCK_DIST)
        actions = build_actions(manifest, {'mock-pip': env})

        wsl_actions = [a for a in actions if a.distro is not None]
        assert len(wsl_actions) == 1
        assert wsl_actions[0].description.startswith('[WSL:Debian] ')

    @staticmethod
    def test_wsl_and_host_actions_coexist() -> None:
        """Both host and WSL actions are produced for the same manifest."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'packages': {'python': ['requests']},
            'wsl2': {
                'Ubuntu': {
                    'packages': {'python': ['flask']},
                },
            },
        })
        env = MockPythonEnv(MOCK_DIST)
        actions = build_actions(manifest, {'mock-pip': env})

        host_actions = [a for a in actions if a.distro is None and a.kind is not None]
        wsl_actions = [a for a in actions if a.distro is not None]

        assert len(host_actions) == 1
        assert len(wsl_actions) == 1
        assert str(host_actions[0].package) == 'requests'
        assert str(wsl_actions[0].package) == 'flask'

    @staticmethod
    def test_wsl_multiple_distros_produce_separate_actions() -> None:
        """Each distro produces its own set of actions."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {'packages': {'python': ['requests']}},
                'Debian': {'packages': {'python': ['flask']}},
            },
        })
        env = MockPythonEnv(MOCK_DIST)
        actions = build_actions(manifest, {'mock-pip': env})

        ubuntu_actions = [a for a in actions if a.distro == 'Ubuntu']
        debian_actions = [a for a in actions if a.distro == 'Debian']

        assert len(ubuntu_actions) == 1
        assert len(debian_actions) == 1
        assert str(ubuntu_actions[0].package) == 'requests'
        assert str(debian_actions[0].package) == 'flask'

    @staticmethod
    def test_wsl_project_action() -> None:
        """WSL project actions have distro set."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {
                    'projects': {'python': []},
                },
            },
        })
        # MockProjectEnv isn't an Environment, just pass empty environments
        actions = build_actions(manifest, {})

        wsl_projects = [a for a in actions if a.distro is not None and a.kind == PluginKind.PROJECT]
        assert len(wsl_projects) == 1
        assert wsl_projects[0].distro == 'Ubuntu'

    @staticmethod
    def test_wsl_actions_use_distro_preferences() -> None:
        """WSL actions use the per-distro preferences for resolver."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {
                    'packages': {'python': ['requests']},
                    'preferences': {'python': 'mock-pip'},
                },
            },
        })
        pip = MockPythonEnv(MOCK_DIST)
        environments = {'mock-pip': pip}
        with patch.object(type(pip), 'is_available', return_value=True):
            actions = build_actions(manifest, environments)

        wsl_actions = [a for a in actions if a.distro == 'Ubuntu']
        assert len(wsl_actions) == 1
        # The WSL distro prefers 'mock-pip' - verify it resolved
        assert wsl_actions[0].installer == 'mock-pip'

    @staticmethod
    def test_wsl_upgrade_strategy_verb() -> None:
        """WSL actions use the correct strategy verb."""
        manifest = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu': {'packages': {'python': ['requests']}},
            },
        })
        env = MockPythonEnv(MOCK_DIST)
        actions = build_actions(manifest, {'mock-pip': env}, SyncStrategy.LATEST)

        wsl_actions = [a for a in actions if a.distro is not None]
        assert 'Upgrade' in wsl_actions[0].description


# ---------------------------------------------------------------------------
# Native WSL2 detection
# ---------------------------------------------------------------------------


class TestNativeDistro:
    """native_distro() returns distro name when inside WSL, None otherwise."""

    @staticmethod
    def test_inside_matching_wsl() -> None:
        native_distro.cache_clear()
        with (
            patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=True),
            patch('porringer.plugin.wsl.utility.get_wsl_distro_name', return_value='Ubuntu-22.04'),
        ):
            assert native_distro() == 'Ubuntu-22.04'
        native_distro.cache_clear()

    @staticmethod
    def test_not_inside_wsl() -> None:
        native_distro.cache_clear()
        with patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=False):
            assert native_distro() is None
        native_distro.cache_clear()

    @staticmethod
    def test_inside_wsl_no_env_var() -> None:
        native_distro.cache_clear()
        with (
            patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=True),
            patch('porringer.plugin.wsl.utility.get_wsl_distro_name', return_value=None),
        ):
            assert native_distro() is None
        native_distro.cache_clear()


class TestWslTransportForDistro:
    """_wsl_transport_for returns WslTransport or None for native."""

    @staticmethod
    def test_returns_wsl_transport_when_not_native() -> None:
        with patch('porringer.plugin.wsl.utility.native_distro', return_value=None):
            transport = _wsl_transport_for('Ubuntu')
            assert isinstance(transport, WslTransport)
            assert transport.distro == 'Ubuntu'

    @staticmethod
    def test_returns_none_when_native() -> None:
        with patch('porringer.plugin.wsl.utility.native_distro', return_value='Ubuntu'):
            assert _wsl_transport_for('Ubuntu') is None

    @staticmethod
    def test_returns_transport_when_different_distro() -> None:
        with patch('porringer.plugin.wsl.utility.native_distro', return_value='Debian'):
            transport = _wsl_transport_for('Ubuntu')
            assert isinstance(transport, WslTransport)


class TestOverlayWslEnvironment:
    """_overlay_wsl_plugin applies or skips WslTransport based on native detection."""

    @staticmethod
    def test_wraps_with_wsl_transport() -> None:
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.plugin.wsl.utility.native_distro', return_value=None):
            result = _overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert result is not envs
        assert isinstance(result['mock-pip']._transport, WslTransport)

    @staticmethod
    def test_skips_when_native() -> None:
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.plugin.wsl.utility.native_distro', return_value='Ubuntu'):
            result = _overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert result is envs  # unchanged — same dict object

    @staticmethod
    def test_wraps_when_different_distro() -> None:
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.plugin.wsl.utility.native_distro', return_value='Debian'):
            result = _overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert isinstance(result['mock-pip']._transport, WslTransport)


# ---------------------------------------------------------------------------
# Per-distro RuntimeContext isolation
# ---------------------------------------------------------------------------


class TestWslRuntimeContexts:
    """Per-distro RuntimeContext on ResolutionContext."""

    @staticmethod
    def test_resolution_context_carries_wsl_contexts() -> None:
        from porringer.backend.command.core.resolution import ResolutionContext

        wsl_ctxs = {
            'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3')}),
        }
        ctx = ResolutionContext(wsl_runtime_contexts=wsl_ctxs)
        assert ctx.wsl_runtime_contexts is not None
        assert 'Ubuntu' in ctx.wsl_runtime_contexts

    @staticmethod
    def test_resolution_context_defaults_to_none() -> None:
        from porringer.backend.command.core.resolution import ResolutionContext

        ctx = ResolutionContext()
        assert ctx.wsl_runtime_contexts is None

    @staticmethod
    def test_wsl_runtime_context_is_separate_from_host() -> None:
        from porringer.backend.command.core.resolution import ResolutionContext

        host_ctx = RuntimeContext(executables={'python': Path('C:\\Python314\\python.exe')})
        wsl_ctxs = {
            'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3.12')}),
        }
        ctx = ResolutionContext(runtime_context=host_ctx, wsl_runtime_contexts=wsl_ctxs)

        # Host and WSL contexts are independent
        assert ctx.runtime_context.get('python') == Path('C:\\Python314\\python.exe')
        assert ctx.wsl_runtime_contexts['Ubuntu'].get('python') == Path('/usr/bin/python3.12')

    @staticmethod
    def test_replace_preserves_wsl_contexts() -> None:
        from porringer.backend.command.core.resolution import ResolutionContext

        wsl_ctxs = {'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3')})}
        ctx = ResolutionContext(wsl_runtime_contexts=wsl_ctxs)
        new_ctx = replace(ctx, runtime_context=RuntimeContext())
        assert new_ctx.wsl_runtime_contexts is wsl_ctxs


class TestExecutionStateWslRuntimes:
    """ExecutionState.wsl_runtime_contexts field and propagation."""

    @staticmethod
    def test_wsl_runtime_contexts_defaults_empty() -> None:
        """The field defaults to an empty dict."""
        from unittest.mock import MagicMock

        from porringer.backend.command.core.execution import ExecutionState

        state = ExecutionState(
            actions=[],
            plugins=MagicMock(),
            parameters=MagicMock(),
            event_queue=MagicMock(),
            manifest_directory=Path('.'),
            preview=MagicMock(),
        )
        assert state.wsl_runtime_contexts == {}

    @staticmethod
    def test_resolution_context_includes_wsl_contexts() -> None:
        """resolution_context property includes wsl_runtime_contexts when populated."""
        from unittest.mock import MagicMock

        from porringer.backend.command.core.execution import ExecutionState

        state = ExecutionState(
            actions=[],
            plugins=MagicMock(project_environments={}),
            parameters=MagicMock(),
            event_queue=MagicMock(),
            manifest_directory=Path('.'),
            preview=MagicMock(),
        )
        state.wsl_runtime_contexts = {
            'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3')}),
        }
        ctx = state.resolution_context
        assert ctx.wsl_runtime_contexts is not None
        assert 'Ubuntu' in ctx.wsl_runtime_contexts

    @staticmethod
    def test_resolution_context_none_when_empty() -> None:
        """resolution_context.wsl_runtime_contexts is None when dict is empty."""
        from unittest.mock import MagicMock

        from porringer.backend.command.core.execution import ExecutionState

        state = ExecutionState(
            actions=[],
            plugins=MagicMock(project_environments={}),
            parameters=MagicMock(),
            event_queue=MagicMock(),
            manifest_directory=Path('.'),
            preview=MagicMock(),
        )
        ctx = state.resolution_context
        assert ctx.wsl_runtime_contexts is None
