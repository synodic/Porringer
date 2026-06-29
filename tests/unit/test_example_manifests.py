"""Validate example manifests against the Pydantic manifest model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from porringer.schema.manifest import SetupManifest

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / 'examples'
EXAMPLE_MANIFESTS = sorted(EXAMPLES_DIR.glob('*/porringer.json'))


@pytest.mark.parametrize('manifest_path', EXAMPLE_MANIFESTS, ids=lambda p: p.parent.name)
def test_example_parses_with_pydantic(manifest_path: Path) -> None:
    """Each example manifest parses without error via SetupManifest."""
    raw = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest = SetupManifest.model_validate(raw)
    assert manifest.version == '1'
