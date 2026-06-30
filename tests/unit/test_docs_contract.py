"""Tests that keep documentation examples aligned with public surfaces."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import porringer
from porringer.schema.manifest import SetupManifest

DOCS_ROOT = Path(__file__).resolve().parents[2] / 'docs'
PYTHON_FENCE_RE = re.compile(r'```[ \t]*(?:python|py)\b[^\n]*\n(.*?)```', re.DOTALL)


def _docs_file(name: str) -> str:
    """Read a docs page as UTF-8 text."""
    return (DOCS_ROOT / name).read_text(encoding='utf-8')


def test_documented_command_index_matches_cli_surface() -> None:
    """The command index names the current top-level CLI surfaces."""
    index = _docs_file('index.md')
    expected_commands = {
        'porringer plugin list',
        'porringer package',
        'porringer preview',
        'porringer install',
        'porringer open',
        'porringer check',
        'porringer download',
        'porringer env info',
        'porringer schema',
        'porringer self check',
    }

    for command in expected_commands:
        assert f'`{command}`' in index

    assert '`porringer self update`' not in index
    assert '`porringer sync`' not in index
    assert '`porringer inspect`' not in index


def test_python_docs_examples_compile() -> None:
    """Python code blocks in docs remain syntactically valid."""
    for path in sorted(DOCS_ROOT.glob('*.md')):
        text = path.read_text(encoding='utf-8')
        for index, match in enumerate(PYTHON_FENCE_RE.finditer(text), start=1):
            source = match.group(1)
            compile(source, f'{path.name}:python-block-{index}', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_install_md_schema_table_covers_all_manifest_fields() -> None:
    """install.md schema table mentions every top-level SetupManifest field.

    This test prevents docs drift when new fields are added to SetupManifest
    but the install.md table is not updated.
    """
    install = _docs_file('install.md')

    # Collect the field names defined on SetupManifest (exclude private attrs).
    manifest_fields = {name for name in SetupManifest.model_fields if not name.startswith('_')}

    # Each field should appear as a backtick-wrapped cell in the table.
    missing = [f for f in manifest_fields if f'`{f}`' not in install]
    assert missing == [], (
        f'install.md manifest schema table is missing fields: {missing}. '
        'Add a row for each missing field to the "Manifest Schema" table.'
    )


def test_top_level_exception_exports_documented_in_api_md() -> None:
    """api.md documents the exceptions exported from the porringer top-level package."""
    api = _docs_file('api.md')

    # Check that each exported exception name appears in api.md.
    exception_names = [name for name in porringer.__all__ if 'Error' in name or 'Code' in name]
    missing = [name for name in exception_names if name not in api]
    assert missing == [], f'api.md does not document the following exported exceptions: {missing}'


def test_version_exported_from_top_level() -> None:
    """porringer.__version__ is a non-empty string."""
    assert isinstance(porringer.__version__, str)
    assert porringer.__version__  # not empty
