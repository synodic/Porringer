"""Helpers for test presence detection.

Tests for presence detection edge cases.

Covers the behaviour of ``is_package_installed()`` and
``PIPEnvironment.packages()`` when the underlying subprocess calls
fail — as happens when ``sys.executable`` is a frozen binary.
"""

import json
from unittest.mock import AsyncMock, patch

from packaging.version import Version

from porringer.backend.command.core.resolution import is_package_installed
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Distribution, Package, PackageRef, PluginKind, PluginParameters
from porringer.plugin.pip.plugin import PIPEnvironment

_DIST = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


class TestIsPackageInstalledEdgeCases:
    """Boundary cases for ``is_package_installed``."""

    @staticmethod
    def test_empty_installed_list_returns_false() -> None:
        """An empty package list always means 'not installed'."""
        installed, detail, matched = is_package_installed(
            PackageRef(name='pipx'),
            [],
        )
        assert installed is False
        assert detail is None
        assert matched is None

    @staticmethod
    def test_package_present_in_list_returns_true() -> None:
        """Baseline: a matching name in the list is detected."""
        installed, detail, matched = is_package_installed(
            PackageRef(name='pipx'),
            [Package(name='pipx', version='1.7.0')],
            name_validator='pep440',
        )
        assert installed is True
        assert matched is not None
        assert matched.name == 'pipx'

    @staticmethod
    def test_case_insensitive_pep440_match() -> None:
        """PEP 440 canonicalization normalises case."""
        installed, _, matched = is_package_installed(
            PackageRef(name='Pipx'),
            [Package(name='pipx', version='1.7.0')],
            name_validator='pep440',
        )
        assert installed is True

    @staticmethod
    def test_runtime_prefix_matching() -> None:
        """RUNTIME kind uses prefix matching (e.g. '3.14' matches '3.14-64')."""
        installed, _, matched = is_package_installed(
            PackageRef(name='3.14'),
            [Package(name='3.14-64', version='3.14.0')],
            kind=PluginKind.RUNTIME,
        )
        assert installed is True


class TestPipPackagesSubprocessFailure:
    """``PIPEnvironment.packages()`` must return ``[]`` when subprocess fails."""

    @staticmethod
    async def test_packages_returns_empty_when_pip_fails() -> None:
        """Both pip and importlib fallback fail → empty list, no exception."""
        env = PIPEnvironment(_DIST)

        # Mock a failing subprocess for both pip and importlib paths
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error'))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await env.packages(runtime_context=RuntimeContext())

        assert result == []

    @staticmethod
    async def test_packages_returns_empty_when_binary_not_found() -> None:
        """FileNotFoundError from subprocess (e.g. missing binary) → empty list."""
        env = PIPEnvironment(_DIST)

        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError('No such file')):
            result = await env.packages(runtime_context=RuntimeContext())

        assert result == []

    @staticmethod
    async def test_packages_returns_list_when_pip_succeeds() -> None:
        """Baseline: successful pip list returns actual packages."""
        env = PIPEnvironment(_DIST)
        payload = json.dumps([{'name': 'pipx', 'version': '1.7.0'}]).encode()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(payload, b''))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await env.packages(runtime_context=RuntimeContext())

        assert len(result) == 1
        assert result[0].name == 'pipx'
