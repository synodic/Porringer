"""Fixtures for smoke tests that need disposable user/tool homes."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from porringer.utility.tool_environment import DISPOSABLE_ENVIRONMENT_BINDINGS


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    """Return paths in order, removing duplicates with platform-aware comparison."""
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower() if os.name == 'nt' else str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _path_parent_for_tool(tool_name: str, *, search_path: str) -> Path | None:
    """Return the parent directory for *tool_name* on *search_path*, if found."""
    tool_path = shutil.which(tool_name, path=search_path)
    if tool_path is None:
        return None
    return Path(tool_path).parent


@dataclass(slots=True)
class DisposableToolEnvironment:
    """Isolated user and tool-home directories for real wrapper smoke tests."""

    root: Path
    monkeypatch: pytest.MonkeyPatch = field(repr=False)
    original_path: str

    home: Path = field(init=False)
    appdata: Path = field(init=False)
    localappdata: Path = field(init=False)
    xdg_config_home: Path = field(init=False)
    xdg_data_home: Path = field(init=False)
    xdg_cache_home: Path = field(init=False)
    bin_dir: Path = field(init=False)
    pipx_home: Path = field(init=False)
    pipx_bin_dir: Path = field(init=False)
    pdm_home: Path = field(init=False)
    uv_cache_dir: Path = field(init=False)
    npm_config_prefix: Path = field(init=False)
    pnpm_home: Path = field(init=False)
    path_entries: tuple[Path, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        """Create the directory layout used by the disposable environment."""
        self.home = self.root / 'home'
        self.appdata = self.root / 'appdata'
        self.localappdata = self.root / 'localappdata'
        self.xdg_config_home = self.root / 'xdg' / 'config'
        self.xdg_data_home = self.root / 'xdg' / 'data'
        self.xdg_cache_home = self.root / 'xdg' / 'cache'
        self.bin_dir = self.root / 'bin'
        self.pipx_home = self.root / 'pipx' / 'home'
        self.pipx_bin_dir = self.root / 'pipx' / 'bin'
        self.pdm_home = self.root / 'pdm'
        self.uv_cache_dir = self.root / 'uv-cache'
        self.npm_config_prefix = self.root / 'npm-prefix'
        self.pnpm_home = self.root / 'pnpm'

        for path in self.directories:
            path.mkdir(parents=True, exist_ok=True)

    @property
    def directories(self) -> tuple[Path, ...]:
        """Return every directory created for this disposable environment."""
        return (
            self.home,
            self.appdata,
            self.localappdata,
            self.xdg_config_home,
            self.xdg_data_home,
            self.xdg_cache_home,
            self.bin_dir,
            self.pipx_home,
            self.pipx_bin_dir,
            self.pdm_home,
            self.uv_cache_dir,
            self.npm_config_prefix,
            self.npm_bin_dir,
            self.pnpm_home,
        )

    @property
    def npm_bin_dir(self) -> Path:
        """Return the npm global executable directory for this platform."""
        if os.name == 'nt':
            return self.npm_config_prefix
        return self.npm_config_prefix / 'bin'

    @property
    def tool_bin_dirs(self) -> tuple[Path, ...]:
        """Return directories where tools are expected to expose executables."""
        return (
            self.bin_dir,
            self.pipx_bin_dir,
            self.npm_bin_dir,
            self.pnpm_home,
        )

    def env_vars(self) -> dict[str, str]:
        """Return environment variables that isolate user and tool state."""
        return {name: str(getattr(self, attr)) for name, attr in DISPOSABLE_ENVIRONMENT_BINDINGS.items()}

    def activate(
        self,
        *,
        allowed_host_tools: Sequence[str] = (),
        extra_path_entries: Sequence[Path] = (),
    ) -> None:
        """Apply the disposable environment to ``os.environ`` via ``monkeypatch``.

        Args:
            allowed_host_tools: Host tools whose containing directories
                should be preserved on PATH.  All other original PATH
                entries are hidden.
            extra_path_entries: Additional directories to prepend to
                PATH, useful for test-specific shims.
        """
        host_tool_dirs = [
            parent
            for tool_name in allowed_host_tools
            if (parent := _path_parent_for_tool(tool_name, search_path=self.original_path)) is not None
        ]
        path_entries = _dedupe_paths([*extra_path_entries, *self.tool_bin_dirs, *host_tool_dirs])

        for name, value in self.env_vars().items():
            self.monkeypatch.setenv(name, value)
        self.monkeypatch.setenv('PATH', os.pathsep.join(str(path) for path in path_entries))
        self.path_entries = tuple(path_entries)


DisposableToolEnvironmentFactory = Callable[..., DisposableToolEnvironment]


@pytest.fixture
def disposable_tool_environment_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DisposableToolEnvironmentFactory:
    """Return a factory for isolated user/tool-home environments."""

    def _factory(
        name: str = 'tool-env',
        *,
        allowed_host_tools: Sequence[str] = (),
        extra_path_entries: Sequence[Path] = (),
    ) -> DisposableToolEnvironment:
        env = DisposableToolEnvironment(
            root=tmp_path / name,
            monkeypatch=monkeypatch,
            original_path=os.environ.get('PATH', ''),
        )
        env.activate(allowed_host_tools=allowed_host_tools, extra_path_entries=extra_path_entries)
        return env

    return _factory


@pytest.fixture
def disposable_tool_environment(
    disposable_tool_environment_factory: DisposableToolEnvironmentFactory,
) -> DisposableToolEnvironment:
    """Return a disposable environment with only temporary tool bins on PATH."""
    return disposable_tool_environment_factory()
