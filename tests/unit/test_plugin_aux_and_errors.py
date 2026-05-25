"""Auxiliary-tool absence and subprocess-failure tolerance matrix.

Verifies two cross-plugin invariants without invoking any wrapped CLI:

1. **Aux tool absence** \u2014 a plugin that declares ``auxiliary_tools()``
   must continue to function (no exceptions) when those tools are
   missing from PATH.  Today only :class:`PIPEnvironment` declares
   any, but the test is parametrized so future declarations are
   covered automatically.

2. **Tool absence tolerance** \u2014 ``Environment.packages()`` must
   degrade gracefully (return ``[]``, not raise) when the wrapped
   CLI's subprocess invocation fails with ``FileNotFoundError``.
   This catches the regression where a plugin forgets to wrap
   ``asyncio.create_subprocess_exec`` in a guard.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from porringer.backend.command.core.discovery import discover_all_plugins
from porringer.core.plugin_schema.environment import Environment, PackageParameters
from porringer.core.schema import PackageRef


def _env_pairs() -> list[tuple[str, Environment]]:
    discovered = discover_all_plugins(use_cache=False)
    return sorted(discovered.environments.items())


_ENV_PAIRS = _env_pairs()

_AUX_PAIRS = [(n, p) for n, p in _ENV_PAIRS if type(p).auxiliary_tools()]
_AUX_IDS = [n for n, _ in _AUX_PAIRS]

# Plugins whose ``packages()`` reads directly from the filesystem rather
# than spawning the wrapped CLI (e.g. pipx walks ``~/.local/pipx/venvs``).
# These are excluded from the subprocess-failure-tolerance matrix \u2014 the
# test would need to mock the filesystem too, which is plugin-specific.
_FS_BACKED_PACKAGES: frozenset[str] = frozenset({'pipx'})

_SUBPROC_ENV_PAIRS = [(n, p) for n, p in _ENV_PAIRS if n not in _FS_BACKED_PACKAGES]
_SUBPROC_ENV_IDS = [n for n, _ in _SUBPROC_ENV_PAIRS]


@contextmanager
def _hide_tools(tools: tuple[str, ...]) -> Iterator[None]:
    """Patch ``shutil.which`` so the listed tools appear absent."""
    original = shutil.which

    def _restricted(name: str, *args: object, **kwargs: object) -> str | None:
        if name in tools:
            return None
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    with patch('shutil.which', side_effect=_restricted):
        yield


@pytest.mark.skipif(not _AUX_PAIRS, reason='no plugins declare auxiliary tools')
@pytest.mark.parametrize(('name', 'plugin'), _AUX_PAIRS, ids=_AUX_IDS or ['<none>'])
class TestAuxiliaryToolAbsence:
    """Plugins must tolerate every declared auxiliary tool being missing."""

    @staticmethod
    async def test_post_action_tolerates_missing_aux(name: str, plugin: Environment) -> None:
        """``post_action`` does not raise when aux tools are absent."""
        del name
        aux = type(plugin).auxiliary_tools()
        params = PackageParameters(package=PackageRef(name='example'))
        with _hide_tools(tuple(aux)):
            for verb in ('install', 'upgrade', 'uninstall'):
                # Should not raise on success or failure paths.
                await plugin.post_action(verb, params, success=True)  # type: ignore[arg-type]
                await plugin.post_action(verb, params, success=False)  # type: ignore[arg-type]


@pytest.mark.parametrize(('name', 'plugin'), _SUBPROC_ENV_PAIRS, ids=_SUBPROC_ENV_IDS)
class TestPackagesToleratesMissingTool:
    """``packages()`` returns ``[]`` (never raises) when the tool is missing."""

    @staticmethod
    async def test_packages_returns_empty_when_tool_missing(name: str, plugin: Environment) -> None:
        """``packages()`` returns ``[]`` when the underlying tool is unavailable."""
        del name

        async def _missing(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError(2, 'No such file or directory')

        # Patch both the asyncio entry-point and ``shutil.which`` so any
        # pre-flight check also reports the tool as absent.
        with (
            patch('asyncio.create_subprocess_exec', side_effect=_missing),
            patch('shutil.which', return_value=None),
        ):
            try:
                result = await asyncio.wait_for(plugin.packages(), timeout=5)
            except FileNotFoundError as exc:
                pytest.fail(f'{type(plugin).__name__}.packages() raised on missing tool: {exc}')
        assert result == [], f'expected [] when tool missing, got {result!r}'
