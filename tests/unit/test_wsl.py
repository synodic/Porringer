"""Tests for WSL2 transport, manifest sections, and action builder integration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from porringer.backend.builder import Builder
from porringer.backend.command.core.action_builder import build_actions
from porringer.backend.command.core.execution import ExecutionState
from porringer.backend.command.core.resolution import ResolutionContext
from porringer.backend.command.core.wsl_overlay import overlay_wsl_plugin, wsl_transport_for
from porringer.backend.command.package import PackageCommands
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind
from porringer.core.transport import LocalTransport, Transport
from porringer.plugin.wsl.transport import WslTransport
from porringer.plugin.wsl.utility import is_inside_wsl, is_wsl_host, native_distro
from porringer.schema import PackageSpec, SetupManifest, SyncStrategy
from porringer.schema.manifest import WslDistroManifest
from tests.fixtures.mock_plugins import MOCK_DIST, MockPythonEnv, MockRuntimeProvider

_PY = Ecosystem('python')
_SYS = Ecosystem('system')

_EXPECTED_PACKAGE_PAIR = 2
_EXPECTED_DISTRO_PAIR = 2
_EXPECTED_SECTION_PAIR = 2
_EXPECTED_WSL_ACTIONS = 2


# ---------------------------------------------------------------------------
# Transport unit tests
# ---------------------------------------------------------------------------


class TestLocalTransport:
    """LocalTransport is a passthrough."""

    @staticmethod
    def test_transform_args_identity() -> None:
        """Args are returned unchanged."""
        t = LocalTransport()
        args = ['pip', 'install', 'requests']
        assert t.transform_args(args) is args

    @staticmethod
    def test_transform_cwd_identity() -> None:
        """Working directory is returned unchanged."""
        t = LocalTransport()
        cwd = Path('/some/path')
        assert t.transform_cwd(cwd) is cwd

    @staticmethod
    def test_transform_cwd_none() -> None:
        """None cwd passes through."""
        t = LocalTransport()
        assert t.transform_cwd(None) is None

    @staticmethod
    def test_check_tool_delegates_to_which() -> None:
        """Tool check delegates to shutil.which."""
        t = LocalTransport()
        with patch('porringer.core.transport.shutil.which', return_value='/usr/bin/git'):
            assert t.check_tool('git') is True
        with patch('porringer.core.transport.shutil.which', return_value=None):
            assert t.check_tool('nonexistent') is False

    @staticmethod
    def test_is_transport_protocol() -> None:
        """LocalTransport satisfies the Transport protocol."""
        assert isinstance(LocalTransport(), Transport)


class TestWslTransport:
    """WslTransport prepends wsl --exec -d <distro>."""

    @staticmethod
    def test_transform_args_prepends_wsl() -> None:
        """Command args are prefixed with wsl invocation."""
        t = WslTransport('Ubuntu-22.04')
        result = t.transform_args(['apt', 'install', 'curl'])
        assert result == ['wsl', '--exec', '-d', 'Ubuntu-22.04', 'apt', 'install', 'curl']

    @staticmethod
    def test_transform_args_empty() -> None:
        """Empty args produce bare wsl invocation."""
        t = WslTransport('Debian')
        result = t.transform_args([])
        assert result == ['wsl', '--exec', '-d', 'Debian']

    @staticmethod
    def test_distro_property() -> None:
        """Distro name is accessible via property."""
        t = WslTransport('Ubuntu-22.04')
        assert t.distro == 'Ubuntu-22.04'

    @staticmethod
    def test_transform_cwd_none() -> None:
        """None cwd passes through."""
        t = WslTransport('Ubuntu')
        assert t.transform_cwd(None) is None

    @staticmethod
    def test_transform_cwd_posix_passthrough() -> None:
        """POSIX paths are returned unchanged."""
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
        """Tool check delegates to wsl_which for the target distro."""
        t = WslTransport('Ubuntu')
        with patch('porringer.plugin.wsl.transport.wsl_which', return_value=True) as mock_which:
            assert t.check_tool('git') is True
            mock_which.assert_called_once_with('Ubuntu', 'git')

    @staticmethod
    def test_is_transport_protocol() -> None:
        """WslTransport satisfies the Transport protocol."""
        assert isinstance(WslTransport('test'), Transport)

    @staticmethod
    def test_repr() -> None:
        """Repr includes the distro name."""
        t = WslTransport('Ubuntu-22.04')
        assert 'Ubuntu-22.04' in repr(t)


# ---------------------------------------------------------------------------
# Plugin.with_transport
# ---------------------------------------------------------------------------


class TestWithTransport:
    """Plugin.with_transport() creates a new instance with a different transport."""

    @staticmethod
    def test_new_instance_different_transport() -> None:
        """A fresh instance with the new transport is returned."""
        env = MockRuntimeProvider(MOCK_DIST)
        assert isinstance(env._transport, LocalTransport)

        wsl = WslTransport('Ubuntu')
        new_env = env.with_transport(wsl)

        assert new_env is not env
        assert isinstance(new_env, MockRuntimeProvider)
        assert new_env._transport is wsl

    @staticmethod
    def test_preserves_distribution() -> None:
        """The new instance keeps the same distribution."""
        env = MockRuntimeProvider(MOCK_DIST)
        new_env = env.with_transport(WslTransport('Debian'))
        assert new_env._distribution == env._distribution

    @staticmethod
    def test_original_unchanged() -> None:
        """The original instance retains its LocalTransport."""
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
        """Returns True on Windows when wsl.exe is on PATH."""
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.shutil.which', return_value='C:\\Windows\\system32\\wsl.exe'),
        ):
            mock_sys.platform = 'win32'
            assert is_wsl_host() is True

    @staticmethod
    def test_is_wsl_host_not_windows() -> None:
        """Returns False on non-Windows platforms."""
        with patch('porringer.plugin.wsl.utility.sys') as mock_sys:
            mock_sys.platform = 'linux'
            assert is_wsl_host() is False

    @staticmethod
    def test_is_wsl_host_no_wsl_exe() -> None:
        """Returns False when wsl.exe is not on PATH."""
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.shutil.which', return_value=None),
        ):
            mock_sys.platform = 'win32'
            assert is_wsl_host() is False

    @staticmethod
    def test_is_inside_wsl_linux_with_microsoft() -> None:
        """Returns True when /proc/version contains 'microsoft'."""
        with (
            patch('porringer.plugin.wsl.utility.sys') as mock_sys,
            patch('porringer.plugin.wsl.utility.Path') as mock_path,
        ):
            mock_sys.platform = 'linux'
            mock_path.return_value.read_text.return_value = 'Linux version 5.15.153.1-microsoft-standard-WSL2'
            assert is_inside_wsl() is True

    @staticmethod
    def test_is_inside_wsl_not_linux() -> None:
        """Returns False on non-Linux platforms."""
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
        """All fields default to empty dicts."""
        m = WslDistroManifest()
        assert m.packages == {}
        assert m.tools == {}
        assert m.projects == {}
        assert m.runtimes == {}
        assert m.scm == {}
        assert m.preferences == {}

    @staticmethod
    def test_packages_section() -> None:
        """Packages are parsed into ecosystem-keyed dicts."""
        m = WslDistroManifest.model_validate({
            'packages': {'system': ['curl', 'build-essential']},
        })
        assert len(m.packages[Ecosystem('system')]) == _EXPECTED_PACKAGE_PAIR

    @staticmethod
    def test_preferences_override() -> None:
        """Per-ecosystem preferences are parsed correctly."""
        m = WslDistroManifest.model_validate({
            'packages': {'python': ['requests']},
            'preferences': {'python': 'pip'},
        })
        assert m.preferences[Ecosystem('python')] == 'pip'


class TestSetupManifestWsl2:
    """SetupManifest.wsl2 field integration."""

    @staticmethod
    def test_wsl2_defaults_to_empty() -> None:
        """The wsl2 field defaults to an empty dict."""
        m = SetupManifest()
        assert m.wsl2 == {}

    @staticmethod
    def test_wsl2_single_distro() -> None:
        """A single distro is parsed into the wsl2 dict."""
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
        """Multiple distros are parsed independently."""
        m = SetupManifest.model_validate({
            'version': '1',
            'wsl2': {
                'Ubuntu-22.04': {'packages': {'system': ['curl']}},
                'Debian': {'packages': {'system': ['wget']}},
            },
        })
        assert len(m.wsl2) == _EXPECTED_DISTRO_PAIR

    @staticmethod
    def test_iter_wsl_sections() -> None:
        """iter_wsl_sections yields one tuple per non-empty section."""
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
        assert len(sections) == _EXPECTED_SECTION_PAIR

        # Each entry is (distro, kind, ecosystem, packages)
        distros = {s[0] for s in sections}
        assert distros == {'Ubuntu'}

        kinds = {s[1] for s in sections}
        assert PluginKind.PACKAGE in kinds
        assert PluginKind.RUNTIME in kinds

    @staticmethod
    def test_iter_wsl_sections_empty_when_no_wsl2() -> None:
        """No sections are yielded when wsl2 is absent."""
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
        environments: dict[str, Environment] = {'mock-pip': env}
        actions = build_actions(manifest, environments)

        wsl_actions = [a for a in actions if a.distro is not None]
        assert len(wsl_actions) == _EXPECTED_WSL_ACTIONS
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
        environments: dict[str, Environment] = {'mock-pip': pip}
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
        """Returns the distro name when running inside that distro."""
        native_distro.cache_clear()
        with (
            patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=True),
            patch('porringer.plugin.wsl.utility.get_wsl_distro_name', return_value='Ubuntu-22.04'),
        ):
            assert native_distro() == 'Ubuntu-22.04'
        native_distro.cache_clear()

    @staticmethod
    def test_not_inside_wsl() -> None:
        """Returns None when not inside WSL."""
        native_distro.cache_clear()
        with patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=False):
            assert native_distro() is None
        native_distro.cache_clear()

    @staticmethod
    def test_inside_wsl_no_env_var() -> None:
        """Returns None when inside WSL but env var is unset."""
        native_distro.cache_clear()
        with (
            patch('porringer.plugin.wsl.utility.is_inside_wsl', return_value=True),
            patch('porringer.plugin.wsl.utility.get_wsl_distro_name', return_value=None),
        ):
            assert native_distro() is None
        native_distro.cache_clear()


class TestWslTransportForDistro:
    """wsl_transport_for returns WslTransport or None for native."""

    @staticmethod
    def test_returns_wsl_transport_when_not_native() -> None:
        """Returns a WslTransport when not running natively."""
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value=None):
            transport = wsl_transport_for('Ubuntu')
            assert isinstance(transport, WslTransport)
            assert transport.distro == 'Ubuntu'

    @staticmethod
    def test_returns_none_when_native() -> None:
        """Returns None when already inside the target distro."""
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value='Ubuntu'):
            assert wsl_transport_for('Ubuntu') is None

    @staticmethod
    def test_returns_transport_when_different_distro() -> None:
        """Returns a WslTransport when native distro differs."""
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value='Debian'):
            transport = wsl_transport_for('Ubuntu')
            assert isinstance(transport, WslTransport)


class TestOverlayWslEnvironment:
    """overlay_wsl_plugin applies or skips WslTransport based on native detection."""

    @staticmethod
    def test_wraps_with_wsl_transport() -> None:
        """Installer plugin is wrapped with WslTransport."""
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value=None):
            result = overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert result is not envs
        assert isinstance(result['mock-pip']._transport, WslTransport)

    @staticmethod
    def test_skips_when_native() -> None:
        """Returns the same dict when already inside the target distro."""
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value='Ubuntu'):
            result = overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert result is envs  # unchanged — same dict object

    @staticmethod
    def test_wraps_when_different_distro() -> None:
        """Wraps when native distro differs from target."""
        env = MockPythonEnv(MOCK_DIST)
        envs = {'mock-pip': env}
        with patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value='Debian'):
            result = overlay_wsl_plugin(envs, 'mock-pip', 'Ubuntu')
        assert isinstance(result['mock-pip']._transport, WslTransport)


# ---------------------------------------------------------------------------
# Per-distro RuntimeContext isolation
# ---------------------------------------------------------------------------


class TestWslRuntimeContexts:
    """Per-distro RuntimeContext on ResolutionContext."""

    @staticmethod
    def test_resolution_context_carries_wsl_contexts() -> None:
        """WSL runtime contexts are stored and retrievable."""
        wsl_ctxs = {
            'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3')}),
        }
        ctx = ResolutionContext(wsl_runtime_contexts=wsl_ctxs)
        assert ctx.wsl_runtime_contexts is not None
        assert 'Ubuntu' in ctx.wsl_runtime_contexts

    @staticmethod
    def test_resolution_context_defaults_to_none() -> None:
        """Default ResolutionContext has no WSL contexts."""
        ctx = ResolutionContext()
        assert ctx.wsl_runtime_contexts is None

    @staticmethod
    def test_wsl_runtime_context_is_separate_from_host() -> None:
        """Host and WSL runtime contexts are independent."""
        host_ctx = RuntimeContext(executables={'python': Path('C:\\Python314\\python.exe')})
        wsl_ctxs = {
            'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3.12')}),
        }
        ctx = ResolutionContext(runtime_context=host_ctx, wsl_runtime_contexts=wsl_ctxs)

        assert ctx.runtime_context is not None
        assert ctx.runtime_context.get('python') == Path('C:\\Python314\\python.exe')
        assert ctx.wsl_runtime_contexts is not None
        assert ctx.wsl_runtime_contexts['Ubuntu'].get('python') == Path('/usr/bin/python3.12')

    @staticmethod
    def test_replace_preserves_wsl_contexts() -> None:
        """Dataclass replace preserves wsl_runtime_contexts."""
        wsl_ctxs = {'Ubuntu': RuntimeContext(executables={'python': Path('/usr/bin/python3')})}
        ctx = ResolutionContext(wsl_runtime_contexts=wsl_ctxs)
        new_ctx = replace(ctx, runtime_context=RuntimeContext())
        assert new_ctx.wsl_runtime_contexts is wsl_ctxs


class TestExecutionStateWslRuntimes:
    """ExecutionState.wsl_runtime_contexts field and propagation."""

    @staticmethod
    def test_wsl_runtime_contexts_defaults_empty() -> None:
        """The field defaults to an empty dict."""
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


# ---------------------------------------------------------------------------
# PackageCommands WSL distro parameter
# ---------------------------------------------------------------------------


class TestPackageCommandsListDistro:
    """PackageCommands.list forwards distro to WSL overlay."""

    @staticmethod
    async def test_list_without_distro_unchanged() -> None:
        """Default distro=None preserves existing behaviour."""
        expected = [Package(name='requests', version='2.31.0')]
        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=expected)

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'mock-pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            result = await PackageCommands.list('mock-pip')

        assert result == expected
        # Transport should not have been overlaid
        assert not isinstance(getattr(mock_env, '_transport', None), WslTransport)

    @staticmethod
    async def test_list_with_distro_overlays_transport() -> None:
        """Passing distro= wraps the plugin with WslTransport."""
        expected = [Package(name='requests', version='2.31.0')]
        env = MockPythonEnv(MOCK_DIST)
        envs: dict[str, Environment] = {'mock-pip': env}

        with (
            patch('porringer.backend.command.package._discover_environments', return_value=envs),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
            patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value=None),
            patch.object(type(env), 'query_availability', return_value=True),
            patch.object(type(env), 'packages', new_callable=AsyncMock, return_value=expected),
        ):
            result = await PackageCommands.list('mock-pip', distro='Ubuntu')

        assert result == expected

    @staticmethod
    async def test_list_with_distro_none_when_native() -> None:
        """When already inside the target distro, no overlay happens."""
        expected = [Package(name='requests', version='2.31.0')]
        mock_env = MagicMock(spec=Environment)
        mock_env.query_availability = MagicMock(return_value=True)
        mock_env.packages = AsyncMock(return_value=expected)
        mock_env._transport = LocalTransport()

        with (
            patch('porringer.backend.command.package._discover_environments', return_value={'mock-pip': mock_env}),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
            patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value='Ubuntu'),
        ):
            result = await PackageCommands.list('mock-pip', distro='Ubuntu')

        assert result == expected


class TestPackageCommandsImperativeDistro:
    """install/upgrade/uninstall forward distro to SetupAction."""

    @staticmethod
    async def test_upgrade_sets_distro_on_action() -> None:
        """upgrade(distro='Ubuntu') produces SetupAction.distro == 'Ubuntu'."""
        captured_action = None

        async def _capture_execute(action, *args, **kwargs):
            nonlocal captured_action
            captured_action = action
            return MagicMock(success=True, skipped=False, skip_reason=None, message=None)

        mock_env = MagicMock(spec=Environment)
        mock_env.plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        mock_env.ecosystem = MagicMock(return_value=Ecosystem('python'))

        mock_plugins = MagicMock()
        mock_plugins.environments = {'pip': mock_env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with patch('porringer.backend.command.package.execute_package', side_effect=_capture_execute):
            await PackageCommands.upgrade(
                'pip',
                PackageRef(name='requests'),
                plugins=mock_plugins,
                distro='Ubuntu',
            )

        assert captured_action is not None
        assert captured_action.distro == 'Ubuntu'

    @staticmethod
    async def test_install_sets_distro_on_action() -> None:
        """install(distro='Debian') produces SetupAction.distro == 'Debian'."""
        captured_action = None

        async def _capture_execute(action, *args, **kwargs):
            nonlocal captured_action
            captured_action = action
            return MagicMock(success=True, skipped=False, skip_reason=None, message=None)

        mock_env = MagicMock(spec=Environment)
        mock_env.plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        mock_env.ecosystem = MagicMock(return_value=Ecosystem('python'))

        mock_plugins = MagicMock()
        mock_plugins.environments = {'pip': mock_env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with patch('porringer.backend.command.package.execute_package', side_effect=_capture_execute):
            await PackageCommands.install(
                'pip',
                PackageRef(name='flask'),
                plugins=mock_plugins,
                distro='Debian',
            )

        assert captured_action is not None
        assert captured_action.distro == 'Debian'

    @staticmethod
    async def test_uninstall_sets_distro_on_action() -> None:
        """uninstall(distro='Ubuntu') produces SetupAction.distro == 'Ubuntu'."""
        captured_action = None

        async def _capture_execute(action, *args, **kwargs):
            nonlocal captured_action
            captured_action = action
            return MagicMock(success=True, skipped=False, skip_reason=None, message=None)

        mock_env = MagicMock(spec=Environment)
        mock_env.plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        mock_env.ecosystem = MagicMock(return_value=Ecosystem('python'))

        mock_plugins = MagicMock()
        mock_plugins.environments = {'pip': mock_env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with patch('porringer.backend.command.package.execute_uninstall', side_effect=_capture_execute):
            await PackageCommands.uninstall(
                'pip',
                PackageRef(name='requests'),
                plugins=mock_plugins,
                distro='Ubuntu',
            )

        assert captured_action is not None
        assert captured_action.distro == 'Ubuntu'

    @staticmethod
    async def test_imperative_without_distro_defaults_none() -> None:
        """Without distro=, SetupAction.distro is None (backward compat)."""
        captured_action = None

        async def _capture_execute(action, *args, **kwargs):
            nonlocal captured_action
            captured_action = action
            return MagicMock(success=True, skipped=False, skip_reason=None, message=None)

        mock_env = MagicMock(spec=Environment)
        mock_env.plugin_kind = MagicMock(return_value=PluginKind.PACKAGE)
        mock_env.ecosystem = MagicMock(return_value=Ecosystem('python'))

        mock_plugins = MagicMock()
        mock_plugins.environments = {'pip': mock_env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with patch('porringer.backend.command.package.execute_package', side_effect=_capture_execute):
            await PackageCommands.upgrade(
                'pip',
                PackageRef(name='requests'),
                plugins=mock_plugins,
            )

        assert captured_action is not None
        assert captured_action.distro is None


class TestPackageCommandsCheckUpdatesDistro:
    """check_updates forwards distro to WSL overlay."""

    @staticmethod
    async def test_check_updates_without_distro_unchanged() -> None:
        """Default distro=None preserves existing behaviour."""
        env = MockPythonEnv(MOCK_DIST)

        mock_plugins = MagicMock()
        mock_plugins.environments = {'mock-pip': env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with (
            patch.object(type(env), 'is_available', return_value=True),
            patch.object(env, 'query_availability', return_value=True),
            patch.object(type(env), 'packages', new_callable=AsyncMock, return_value=[]),
            patch.object(type(env), 'check_updates', new_callable=AsyncMock, return_value=[]),
        ):
            results = await PackageCommands.check_updates(plugins=mock_plugins)

        assert len(results) == 1
        assert results[0].plugin == 'mock-pip'

    @staticmethod
    async def test_check_updates_with_distro_overlays_transport() -> None:
        """Passing distro= wraps each plugin with WslTransport."""
        env = MockPythonEnv(MOCK_DIST)

        mock_plugins = MagicMock()
        mock_plugins.environments = {'mock-pip': env}
        mock_plugins.resolved_runtime = MagicMock(return_value=RuntimeContext())

        with (
            patch.object(type(env), 'is_available', return_value=True),
            patch.object(type(env), 'query_availability', return_value=True),
            patch.object(type(env), 'packages', new_callable=AsyncMock, return_value=[]),
            patch.object(type(env), 'check_updates', new_callable=AsyncMock, return_value=[]),
            patch('porringer.backend.command.core.wsl_overlay.native_distro', return_value=None),
        ):
            results = await PackageCommands.check_updates(plugins=mock_plugins, distro='Ubuntu')

        assert len(results) == 1
        assert results[0].plugin == 'mock-pip'
