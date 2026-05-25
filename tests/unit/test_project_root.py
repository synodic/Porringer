"""Helpers for test project root."""

"""Tests for per-ecosystem project root auto-discovery.

The `ProjectEnvironment.resolve_project_root()` method walks ancestor
directories from a starting point looking for an ecosystem-specific
marker file (e.g. `package.json` for Node, `pyproject.toml` for
Python).  These tests verify the discovery logic, marker defaults,
boundary handling, and fallback behaviour.
"""

from pathlib import Path

from porringer.core.plugin_schema.project_environment import (
    ECOSYSTEM_MARKERS,
)
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem
from porringer.plugin.poetry.plugin import PoetryEnvironment
from porringer.test.mock.project_environment import MockProjectEnvironment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NodeProjectEnv(MockProjectEnvironment):
    """Mock project environment for the `node` ecosystem."""

    @staticmethod
    def ecosystem() -> Ecosystem:
        return Ecosystem('node')

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        return 'node'


class _UnknownProjectEnv(MockProjectEnvironment):
    """Mock project environment for an unregistered ecosystem."""

    @staticmethod
    def ecosystem() -> Ecosystem:
        return Ecosystem('unknown_ecosystem')

    @classmethod
    def consumed_runtime_kind(cls) -> str:
        return 'unknown_ecosystem'


class _EvidenceProjectEnv(MockProjectEnvironment):
    """Mock project environment with pyproject.toml table evidence."""

    _pyproject_tool_tables = (('tool', 'mock-project'),)


# ---------------------------------------------------------------------------
# project_marker() tests
# ---------------------------------------------------------------------------


class TestProjectMarker:
    """Tests for the `project_marker()` classmethod."""

    @staticmethod
    def test_python_marker() -> None:
        """Python ecosystem maps to pyproject.toml."""
        assert MockProjectEnvironment.project_marker() == 'pyproject.toml'

    @staticmethod
    def test_node_marker() -> None:
        """Node ecosystem maps to package.json."""
        assert _NodeProjectEnv.project_marker() == 'package.json'

    @staticmethod
    def test_unknown_ecosystem_returns_none() -> None:
        """An ecosystem with no registered marker returns None."""
        assert _UnknownProjectEnv.project_marker() is None

    @staticmethod
    def test_all_known_ecosystems_covered() -> None:
        """Every entry in ECOSYSTEM_MARKERS is reachable."""
        assert set(ECOSYSTEM_MARKERS.keys()) == {Ecosystem('python'), Ecosystem('node')}


# ---------------------------------------------------------------------------
# resolve_project_root() tests
# ---------------------------------------------------------------------------


class TestResolveProjectRoot:
    """Tests for the `resolve_project_root()` classmethod."""

    @staticmethod
    def test_marker_at_search_dir(tmp_path: Path) -> None:
        """When the marker is in the search directory itself, return it."""
        (tmp_path / 'pyproject.toml').touch()
        result = MockProjectEnvironment.resolve_project_root(tmp_path)
        assert result == tmp_path

    @staticmethod
    def test_marker_in_parent(tmp_path: Path) -> None:
        """When the marker is one level up, return the parent."""
        (tmp_path / 'pyproject.toml').touch()
        sub = tmp_path / 'tools'
        sub.mkdir()
        result = MockProjectEnvironment.resolve_project_root(sub)
        assert result == tmp_path

    @staticmethod
    def test_marker_two_levels_up(tmp_path: Path) -> None:
        """When the marker is two levels up, return the grandparent."""
        (tmp_path / 'pyproject.toml').touch()
        deep = tmp_path / 'a' / 'b'
        deep.mkdir(parents=True)
        result = MockProjectEnvironment.resolve_project_root(deep)
        assert result == tmp_path

    @staticmethod
    def test_no_marker_returns_none(tmp_path: Path) -> None:
        """When no marker file exists in the hierarchy, return None."""
        sub = tmp_path / 'a' / 'b'
        sub.mkdir(parents=True)
        result = MockProjectEnvironment.resolve_project_root(sub)
        assert result is None

    @staticmethod
    def test_boundary_limits_search(tmp_path: Path) -> None:
        """When the marker is above the boundary, return None."""
        (tmp_path / 'pyproject.toml').touch()
        child = tmp_path / 'child'
        child.mkdir()
        grandchild = child / 'grandchild'
        grandchild.mkdir()
        # Marker is at tmp_path but boundary is child — should not find it
        result = MockProjectEnvironment.resolve_project_root(grandchild, boundary=child)
        assert result is None

    @staticmethod
    def test_boundary_inclusive(tmp_path: Path) -> None:
        """The boundary directory itself is searched."""
        child = tmp_path / 'child'
        child.mkdir()
        (child / 'pyproject.toml').touch()
        grandchild = child / 'grandchild'
        grandchild.mkdir()
        result = MockProjectEnvironment.resolve_project_root(grandchild, boundary=child)
        assert result == child

    @staticmethod
    def test_node_marker_package_json(tmp_path: Path) -> None:
        """Node plugin finds package.json as project root."""
        (tmp_path / 'package.json').write_text('{}')
        tools = tmp_path / 'tools'
        tools.mkdir()
        result = _NodeProjectEnv.resolve_project_root(tools)
        assert result == tmp_path

    @staticmethod
    def test_unknown_ecosystem_returns_none(tmp_path: Path) -> None:
        """Plugin with no marker mapping always returns None."""
        result = _UnknownProjectEnv.resolve_project_root(tmp_path)
        assert result is None

    @staticmethod
    def test_closest_marker_wins(tmp_path: Path) -> None:
        """When multiple directories have the marker, the closest one wins."""
        # Create markers at both root and child
        (tmp_path / 'pyproject.toml').touch()
        child = tmp_path / 'child'
        child.mkdir()
        (child / 'pyproject.toml').touch()
        grandchild = child / 'grandchild'
        grandchild.mkdir()
        result = MockProjectEnvironment.resolve_project_root(grandchild)
        assert result == child

    @staticmethod
    def test_different_ecosystems_find_different_roots(tmp_path: Path) -> None:
        """Python and Node plugins can discover different root dirs."""
        # Python project root at tmp_path
        (tmp_path / 'pyproject.toml').touch()
        # Node project root at tmp_path/frontend
        frontend = tmp_path / 'frontend'
        frontend.mkdir()
        (frontend / 'package.json').write_text('{}')
        manifest_dir = frontend / 'tools'
        manifest_dir.mkdir()

        python_root = MockProjectEnvironment.resolve_project_root(manifest_dir)
        node_root = _NodeProjectEnv.resolve_project_root(manifest_dir)

        # Python finds pyproject.toml at tmp_path
        assert python_root == tmp_path
        # Node finds package.json at frontend
        assert node_root == frontend

    @staticmethod
    def test_project_evidence_from_pyproject_tool_table(tmp_path: Path) -> None:
        """Tool-specific pyproject.toml config identifies the project manager."""
        (tmp_path / 'pyproject.toml').write_text('[tool.mock-project]\n', encoding='utf-8')

        assert _EvidenceProjectEnv.project_evidence(tmp_path) is True

    @staticmethod
    def test_poetry_command_plan_sets_runtime_before_install(tmp_path: Path) -> None:
        """Poetry sync keeps its separate runtime-selection step."""
        (tmp_path / 'pyproject.toml').touch()
        python = tmp_path / 'python.exe'

        plan = PoetryEnvironment.command_plan(tmp_path, runtime_context=RuntimeContext({'python': python}))

        assert plan.directory == tmp_path
        assert plan.steps == [
            ['poetry', 'env', 'use', str(python)],
            ['poetry', 'install'],
        ]
        assert plan.argv == ['poetry', 'install']
