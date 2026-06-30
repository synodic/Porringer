"""Helpers for test manifest discovery.

Test manifest discovery, contributor protocol, has_manifest, and directory validation.
"""

import json
import tempfile
from pathlib import Path

import pytest

from porringer.backend.command.manifest import collect_manifest_contributions, find_manifest, has_manifest
from porringer.backend.command.sync import SyncCommands
from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.schema import Ecosystem, ManifestContribution
from porringer.utility.exception import ManifestError

_PY = Ecosystem('python')


class TestManifestContributor:
    """Tests for the ManifestContributor protocol and plugin-driven discovery."""

    @staticmethod
    def test_manifest_contribution_dataclass() -> None:
        """ManifestContribution stores filename, config_path, and file_format."""
        contrib = ManifestContribution(filename='pyproject.toml', config_path=('tool', 'porringer'), file_format='toml')
        assert contrib.filename == 'pyproject.toml'
        assert contrib.config_path == ('tool', 'porringer')
        assert contrib.file_format == 'toml'

    @staticmethod
    def test_project_environment_is_manifest_contributor() -> None:
        """ProjectEnvironment implements ManifestContributor."""
        assert issubclass(ProjectEnvironment, ManifestContributor)

    @staticmethod
    def test_collect_manifest_contributions_returns_unique_filenames() -> None:
        """collect_manifest_contributions returns deduplicated entries."""
        contributions = collect_manifest_contributions()
        filenames = [c.filename for c in contributions]
        # No duplicates
        assert len(filenames) == len(set(filenames))

    @staticmethod
    def test_collect_manifest_contributions_includes_pyproject() -> None:
        """At least pyproject.toml is contributed by installed Python project plugins."""
        contributions = collect_manifest_contributions()
        filenames = [c.filename for c in contributions]
        assert 'pyproject.toml' in filenames

    @staticmethod
    def test_manifest_filenames_starts_with_native() -> None:
        """manifest_filenames() always starts with 'porringer.json'."""
        filenames = SyncCommands.manifest_filenames()
        assert filenames[0] == 'porringer.json'
        minimum_filename_count = 2  # at least native + pyproject.toml
        assert len(filenames) >= minimum_filename_count

    @staticmethod
    def test_manifest_filenames_includes_contributed() -> None:
        """manifest_filenames() includes plugin-contributed filenames."""
        filenames = SyncCommands.manifest_filenames()
        # With built-in plugins we expect at least pyproject.toml
        assert 'pyproject.toml' in filenames


class TestManifestDiscovery:
    """Tests for find_manifest() with plugin-driven discovery."""

    @staticmethod
    def test_find_native_json_manifest() -> None:
        """find_manifest discovers porringer.json and sets root to parent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'porringer.json'
            path.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == path.resolve()
            assert result.root_directory == Path(tmpdir).resolve()
            assert result.manifest.version == '1'

    @staticmethod
    def test_find_pyproject_inline_manifest() -> None:
        """find_manifest discovers inline [tool.porringer] in pyproject.toml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nversion = "1"\npackages.python = ["requests"]\n')

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == pyproject.resolve()
            assert result.root_directory == Path(tmpdir).resolve()

    @staticmethod
    def test_find_pyproject_reference_manifest() -> None:
        """find_manifest follows manifest reference from pyproject.toml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create referenced manifest in subdirectory
            subdir = Path(tmpdir) / 'tool'
            subdir.mkdir()
            manifest_file = subdir / 'porringer.json'
            manifest_file.write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))

            # Create pyproject.toml with reference
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nmanifest = "tool/porringer.json"\n')

            result = find_manifest(Path(tmpdir))

            # manifest_path points to the actual JSON file
            assert result.manifest_path == manifest_file.resolve()
            # root_directory is the pyproject.toml's parent
            assert result.root_directory == Path(tmpdir).resolve()
            assert result.manifest.version == '1'

    @staticmethod
    def test_find_package_json_inline_manifest() -> None:
        """find_manifest discovers porringer config inside package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_json = Path(tmpdir) / 'package.json'
            pkg_json.write_text(
                json.dumps({
                    'name': 'my-project',
                    'porringer': {'version': '1', 'packages': {'node': ['lodash']}},
                })
            )

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == pkg_json.resolve()
            assert result.root_directory == Path(tmpdir).resolve()
            assert 'node' in result.manifest.packages

    @staticmethod
    def test_native_json_takes_precedence() -> None:
        """porringer.json is preferred over pyproject.toml in same directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            native = Path(tmpdir) / 'porringer.json'
            native.write_text(json.dumps({'version': '1', 'packages': {'python': ['native']}}))

            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nversion = "1"\npackages.python = ["embedded"]\n')

            result = find_manifest(Path(tmpdir))

            # Native JSON wins
            assert result.manifest_path == native.resolve()
            assert 'native' in str(result.manifest.packages.get(_PY, []))

    @staticmethod
    def test_find_manifest_nonexistent_path() -> None:
        """find_manifest raises ManifestError for nonexistent paths."""
        with pytest.raises(ManifestError):
            find_manifest(Path('/nonexistent/path'))

    @staticmethod
    def test_find_manifest_empty_directory() -> None:
        """find_manifest raises ManifestError for empty directories."""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ManifestError):
            find_manifest(Path(tmpdir))

    @staticmethod
    def test_reference_manifest_missing_target() -> None:
        """find_manifest raises ManifestError when reference target doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyproject = Path(tmpdir) / 'pyproject.toml'
            pyproject.write_text('[tool.porringer]\nmanifest = "nonexistent/porringer.json"\n')

            with pytest.raises(ManifestError):
                find_manifest(Path(tmpdir))

    @staticmethod
    def test_package_json_reference_manifest() -> None:
        """find_manifest follows manifest reference from package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / 'config'
            subdir.mkdir()
            manifest_file = subdir / 'porringer.json'
            manifest_file.write_text(json.dumps({'version': '1', 'packages': {'node': ['express']}}))

            pkg_json = Path(tmpdir) / 'package.json'
            pkg_json.write_text(
                json.dumps({
                    'name': 'my-project',
                    'porringer': {'manifest': 'config/porringer.json'},
                })
            )

            result = find_manifest(Path(tmpdir))

            assert result.manifest_path == manifest_file.resolve()
            assert result.root_directory == Path(tmpdir).resolve()


class TestHasManifest:
    """Tests for has_manifest() lightweight check."""

    @staticmethod
    def test_has_manifest_with_native_json() -> None:
        """has_manifest returns True for directory with porringer.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'python': ['requests']}})
            )
            assert has_manifest(Path(tmpdir)) is True

    @staticmethod
    def test_has_manifest_with_pyproject_toml() -> None:
        """has_manifest returns True for directory with [tool.porringer] in pyproject.toml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pyproject.toml').write_text(
                '[tool.porringer]\nversion = "1"\npackages.python = ["requests"]\n'
            )
            assert has_manifest(Path(tmpdir)) is True

    @staticmethod
    def test_has_manifest_empty_directory() -> None:
        """has_manifest returns False for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert has_manifest(Path(tmpdir)) is False

    @staticmethod
    def test_has_manifest_nonexistent_path() -> None:
        """has_manifest returns False for nonexistent path."""
        assert has_manifest(Path('/nonexistent/path')) is False

    @staticmethod
    def test_has_manifest_pyproject_without_porringer() -> None:
        """has_manifest returns False when pyproject.toml has no [tool.porringer]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pyproject.toml').write_text('[project]\nname = "foo"\n')
            assert has_manifest(Path(tmpdir)) is False

    @staticmethod
    def test_sync_commands_has_manifest_delegates() -> None:
        """SyncCommands.has_manifest() delegates to manifest.has_manifest()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'porringer.json').write_text(
                json.dumps({'version': '1', 'packages': {'python': ['requests']}})
            )
            assert SyncCommands.has_manifest(Path(tmpdir)) is True

        with tempfile.TemporaryDirectory() as tmpdir:
            assert SyncCommands.has_manifest(Path(tmpdir)) is False
