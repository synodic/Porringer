"""Helpers for test manifest inspect."""

"""Test state-aware inspection and LATEST/EXACT sync strategies."""

import json
import tempfile
from pathlib import Path

import pytest

from porringer.api import API
from porringer.backend.command.core.action_builder import parse_manifest
from porringer.core.schema import PluginKind
from porringer.schema import InspectionStatus, SetupParameters, SkipReason, SyncStrategy


@pytest.mark.mock_packages
class TestInspectStateAware:
    """Tests for state-aware inspection (detecting already-installed packages)."""

    @staticmethod
    async def test_inspect_marks_installed_package_satisfied(session_api: API) -> None:
        """Test that inspection detects an already-installed package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 'packaging' is always installed (it's a dependency of porringer itself)
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.status == InspectionStatus.SATISFIED
            assert action_result.skip_reason == SkipReason.ALREADY_INSTALLED.name
            assert action_result.message is not None
            assert 'packaging' in action_result.message

    @staticmethod
    async def test_inspect_marks_missing_package_needed(session_api: API) -> None:
        """Test that inspection reports needed for a package that is not installed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # Use a package name that should never be installed
            manifest_data = {'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.skipped is False
            assert action_result.skip_reason is None
            assert action_result.status == InspectionStatus.NEEDED

    @staticmethod
    async def test_inspect_version_satisfied(session_api: API) -> None:
        """Test that inspection reports satisfied when a version specifier is satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # packaging>=1.0 should always be satisfied
            manifest_data = {'version': '1', 'packages': {'python': ['packaging>=1.0']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.status == InspectionStatus.SATISFIED
            assert action_result.skip_reason == SkipReason.ALREADY_INSTALLED.name
            assert action_result.message is not None
            assert 'satisfies' in action_result.message

    @staticmethod
    async def test_inspect_version_not_satisfied(session_api: API) -> None:
        """Test that inspection reports needed when a version specifier is not satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            # packaging>=99999 should never be satisfied
            manifest_data = {'version': '1', 'packages': {'python': ['packaging>=99999']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir))
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.skipped is False
            assert action_result.status == InspectionStatus.NEEDED


@pytest.mark.mock_packages
class TestSyncStrategyUpgrade:
    """Tests for LATEST and EXACT sync strategies."""

    @staticmethod
    def test_preview_exact_strategy_produces_package_actions(session_api: API) -> None:
        """Test that preview with EXACT strategy produces PACKAGE actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir), strategy=SyncStrategy.EXACT)

            assert len(results.actions) == 1
            assert results.actions[0].kind == PluginKind.PACKAGE

    @staticmethod
    async def test_preview_batch_latest_strategy(session_api: API) -> None:
        """Test that batch preview with LATEST strategy threads strategy through."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=Path(tmpdir), strategy=SyncStrategy.LATEST)
            results = await session_api.sync.inspect(params)

            assert len(results.manifests) == 1
            assert results.manifests[0].actions[0].action.kind == PluginKind.PACKAGE.value

    @staticmethod
    def test_upgrade_action_description(session_api: API) -> None:
        """Test that upgrade actions have 'Upgrade' in their description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['requests']}}
            manifest_path.write_text(json.dumps(manifest_data))

            results = parse_manifest(Path(tmpdir), strategy=SyncStrategy.LATEST)

            assert 'Upgrade' in results.actions[0].description

    @staticmethod
    async def test_inspect_latest_installed_package(session_api: API) -> None:
        """Test that inspection of an installed package under latest is satisfied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 'packaging' is always installed
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['packaging']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), strategy=SyncStrategy.LATEST)
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.skipped is True
            assert action_result.skip_reason is not None
            assert action_result.status == InspectionStatus.SATISFIED

    @staticmethod
    async def test_inspect_latest_missing_package(session_api: API) -> None:
        """Test inspection under latest reports install fallback for missing package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'porringer.json'
            manifest_data = {'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}}
            manifest_path.write_text(json.dumps(manifest_data))

            setup_params = SetupParameters(paths=Path(tmpdir), strategy=SyncStrategy.LATEST)
            report = await session_api.sync.inspect(setup_params)

            assert len(report.manifests) == 1
            action_result = report.manifests[0].actions[0]
            assert action_result.success is True
            assert action_result.message is not None
            assert 'install' in action_result.message.lower()
            assert action_result.status == InspectionStatus.NEEDED
