"""Helpers for test target resolution."""

"""Tests for install target resolution helpers."""

from pathlib import Path

import pytest

from porringer.core.target import TargetKind, find_nearest_manifest, parse_link_target, resolve_target


def test_parse_link_target_extracts_profile_url_and_hash() -> None:
    """Install links expose profile URL and sha256 pin."""
    target = parse_link_target('porringer://profile?url=https://example.com/profile.json&sha256=abc123')

    assert target.kind == TargetKind.LINK
    assert target.profile_url == 'https://example.com/profile.json'
    assert target.expected_hash == 'sha256:abc123'


def test_parse_link_target_requires_url() -> None:
    """Missing profile URL is rejected."""
    with pytest.raises(ValueError, match='missing required'):
        parse_link_target('porringer://profile')


def test_resolve_target_uses_nearest_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No target resolves by walking upward to nearest manifest directory."""
    root = tmp_path / 'project'
    nested = root / 'sub' / 'deep'
    nested.mkdir(parents=True)
    (root / 'porringer.json').write_text('{"version":"1"}', encoding='utf-8')

    monkeypatch.chdir(nested)

    target = resolve_target(None)

    assert target.kind == TargetKind.PATH
    assert target.path == root


def test_resolve_target_returns_url() -> None:
    """HTTPS targets are recognized as ambiguous URLs for later sniffing."""
    target = resolve_target('https://example.com/manifest.json')
    assert target.kind == TargetKind.URL
    assert target.url == 'https://example.com/manifest.json'


def test_find_nearest_manifest_raises_when_missing(tmp_path: Path) -> None:
    """Resolution fails when no manifest can be discovered."""
    empty = tmp_path / 'empty'
    empty.mkdir()

    with pytest.raises(ValueError, match='No manifest found'):
        find_nearest_manifest(empty)
