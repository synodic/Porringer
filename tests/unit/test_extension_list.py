"""Helpers for test extension list.

Tests for extension listing (kinds filter) and manifest action-field parsing.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from porringer.api import API
from porringer.backend.builder import Builder
from porringer.backend.command.core.action_builder import parse_manifest
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PluginKind

FIRST_ACTION = 0


class TestListPluginsKinds:
    """Tests for ``extension.list`` kind filtering."""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _skip_tool_version():
        """Bypass ``tool_version()`` subprocess calls — these tests only verify kind filtering."""
        with (
            patch('porringer.backend.resolver.ToolBasedPlugin.tool_version', return_value=None),
            patch.object(Builder, 'resolve_runtime_context', new_callable=AsyncMock, return_value=RuntimeContext()),
        ):
            yield

    @staticmethod
    async def test_kinds_none_returns_all(session_api: API) -> None:
        """When kinds is None, all plugins should be returned."""
        results = await session_api.extension.list()
        assert len(results) > 0

    @staticmethod
    @pytest.mark.parametrize('kind', [PluginKind.PACKAGE, PluginKind.TOOL, PluginKind.PROJECT])
    async def test_kinds_filter_single(session_api: API, kind: PluginKind) -> None:
        """Filtering by a single kind should only return plugins of that kind."""
        results = await session_api.extension.list(kinds=[kind])
        for result in results:
            assert result.kind == kind

    @staticmethod
    async def test_kinds_filter_multiple(session_api: API) -> None:
        """Filtering by multiple kinds should return plugins of all requested kinds."""
        results = await session_api.extension.list(kinds=[PluginKind.PACKAGE, PluginKind.TOOL])
        for result in results:
            assert result.kind in {PluginKind.PACKAGE, PluginKind.TOOL}

    @staticmethod
    async def test_list_results_include_kind(session_api: API) -> None:
        """Every result should have a kind field populated."""
        results = await session_api.extension.list()
        for result in results:
            assert isinstance(result.kind, PluginKind)


@pytest.mark.mock_packages
class TestParseManifestActionFields:
    """parse_manifest exposes installer, kind, ecosystem, and package on each action."""

    @staticmethod
    def test_parse_manifest_action_fields(session_api: API, tmp_path: Path) -> None:
        """parse_manifest actions expose installer, kind, ecosystem, and package."""
        (tmp_path / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

        results = parse_manifest(tmp_path)
        action = results.actions[FIRST_ACTION]

        assert action.kind == PluginKind.PACKAGE
        assert action.ecosystem == 'python'
        assert action.installer is not None
        assert action.package is not None
        assert str(action.package) == 'requests'
