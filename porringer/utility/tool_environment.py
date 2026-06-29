"""Shared tool-environment variable vocabulary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

EnvironmentVariableCategory = Literal[
    'path',
    'user-home',
    'porringer',
    'python-index',
    'pipx',
    'pdm',
    'uv',
    'node',
]


@dataclass(frozen=True, slots=True)
class ToolEnvironmentVariable:
    """Metadata for an environment variable relevant to tool wrappers."""

    name: str
    category: EnvironmentVariableCategory
    include_in_trace: bool = True
    include_in_env_info: bool = True
    disposable_attr: str | None = None


TOOL_ENVIRONMENT_VARIABLES = (
    ToolEnvironmentVariable('PATH', 'path', include_in_env_info=False),
    ToolEnvironmentVariable('HOME', 'user-home', disposable_attr='home'),
    ToolEnvironmentVariable('USERPROFILE', 'user-home', disposable_attr='home'),
    ToolEnvironmentVariable('APPDATA', 'user-home', disposable_attr='appdata'),
    ToolEnvironmentVariable('LOCALAPPDATA', 'user-home', disposable_attr='localappdata'),
    ToolEnvironmentVariable('XDG_CONFIG_HOME', 'user-home', disposable_attr='xdg_config_home'),
    ToolEnvironmentVariable('XDG_DATA_HOME', 'user-home', disposable_attr='xdg_data_home'),
    ToolEnvironmentVariable('XDG_CACHE_HOME', 'user-home', disposable_attr='xdg_cache_home'),
    ToolEnvironmentVariable('PORRINGER_TRACE_DIR', 'porringer'),
    ToolEnvironmentVariable('PIP_FIND_LINKS', 'python-index'),
    ToolEnvironmentVariable('PIP_NO_INDEX', 'python-index'),
    ToolEnvironmentVariable('PIP_DISABLE_PIP_VERSION_CHECK', 'python-index'),
    ToolEnvironmentVariable('PIPX_HOME', 'pipx', disposable_attr='pipx_home'),
    ToolEnvironmentVariable('PIPX_BIN_DIR', 'pipx', disposable_attr='pipx_bin_dir'),
    ToolEnvironmentVariable('PIPX_DEFAULT_PYTHON', 'pipx'),
    ToolEnvironmentVariable('PDM_HOME', 'pdm', disposable_attr='pdm_home'),
    ToolEnvironmentVariable('UV_CACHE_DIR', 'uv', disposable_attr='uv_cache_dir'),
    ToolEnvironmentVariable('UV_FIND_LINKS', 'uv'),
    ToolEnvironmentVariable('UV_NO_INDEX', 'uv'),
    ToolEnvironmentVariable('NPM_CONFIG_PREFIX', 'node', disposable_attr='npm_config_prefix'),
    ToolEnvironmentVariable('PNPM_HOME', 'node', disposable_attr='pnpm_home'),
)

TRACE_ENVIRONMENT_KEYS = tuple(variable.name for variable in TOOL_ENVIRONMENT_VARIABLES if variable.include_in_trace)
ENV_INFO_ENVIRONMENT_KEYS = tuple(
    variable.name for variable in TOOL_ENVIRONMENT_VARIABLES if variable.include_in_env_info
)
DISPOSABLE_ENVIRONMENT_BINDINGS = {
    variable.name: variable.disposable_attr
    for variable in TOOL_ENVIRONMENT_VARIABLES
    if variable.disposable_attr is not None
}


def environment_subset(keys: tuple[str, ...]) -> dict[str, str | None]:
    """Return selected environment values without raising on missing keys."""
    return {key: os.environ.get(key) for key in keys}
