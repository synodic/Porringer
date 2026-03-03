"""Tests for :mod:`porringer.core.path` — system PATH synchronization."""

import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

if sys.platform == 'win32':
    import winreg

from porringer.core.path import (
    ensure_system_path,
    probe_unix_paths,
    read_registry_path,
    reset_sync_state,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_REREAD_CALLS = 2

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sync_state():
    """Reset the module-level ``_synced`` flag before *and* after each test."""
    reset_sync_state()
    yield
    reset_sync_state()


@pytest.fixture
def _fake_path(monkeypatch: pytest.MonkeyPatch):
    """Set a minimal, deterministic PATH for testing."""
    monkeypatch.setenv('PATH', '/usr/bin')


# ---------------------------------------------------------------------------
# ensure_system_path — idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Verify that ``ensure_system_path`` only performs I/O once."""

    @staticmethod
    @pytest.mark.skipif(os.name == 'nt', reason='Unix-only test')
    @pytest.mark.usefixtures('_fake_path')
    def test_second_call_is_noop() -> None:
        """Second consecutive call is a no-op (no additional I/O)."""
        with patch('porringer.core.path.probe_unix_paths', return_value=['/opt/extra']) as probe:
            ensure_system_path()
            ensure_system_path()
        probe.assert_called_once()

    @staticmethod
    @pytest.mark.skipif(os.name == 'nt', reason='Unix-only test')
    @pytest.mark.usefixtures('_fake_path')
    def test_reset_allows_reread() -> None:
        """After ``reset_sync_state()``, the next call re-reads the OS PATH."""
        with patch('porringer.core.path.probe_unix_paths', return_value=['/opt/extra']) as probe:
            ensure_system_path()
            reset_sync_state()
            ensure_system_path()
        assert probe.call_count == _EXPECTED_REREAD_CALLS


# ---------------------------------------------------------------------------
# Windows: registry reading
# ---------------------------------------------------------------------------


class TestWindowsRegistry:
    """Test the Windows registry PATH reading logic."""

    @staticmethod
    @pytest.mark.skipif(os.name != 'nt', reason='Windows-only test')
    def test_reads_system_and_user_path() -> None:
        """Both HKLM (system) and HKCU (user) entries are returned."""
        system_path = r'C:\Program Files\nodejs;C:\Windows\system32'
        user_path = r'%APPDATA%\npm'

        def fake_query(key, name):
            # Return the raw value and a dummy type
            if key == 'system_key':
                return (system_path, winreg.REG_EXPAND_SZ)
            return (user_path, winreg.REG_EXPAND_SZ)

        keys = iter(['system_key', 'user_key'])

        def fake_open(root, sub, reserved, access):
            return MagicMock(__enter__=lambda s: next(keys), __exit__=lambda *a: None)

        with (
            patch('winreg.OpenKey', side_effect=fake_open),
            patch('winreg.QueryValueEx', side_effect=fake_query),
        ):
            result = read_registry_path()

        # %APPDATA% should be expanded
        assert r'%APPDATA%' not in os.pathsep.join(result)
        assert r'C:\Program Files\nodejs' in result
        assert r'C:\Windows\system32' in result

    @staticmethod
    @pytest.mark.skipif(os.name != 'nt', reason='Windows-only test')
    def test_registry_read_merges_into_path(monkeypatch: pytest.MonkeyPatch) -> None:
        """Registry entries missing from the process PATH get added."""
        monkeypatch.setenv('PATH', r'C:\Windows\system32')

        with patch(
            'porringer.core.path.read_registry_path',
            return_value=[r'C:\Windows\system32', r'C:\Program Files\nodejs'],
        ):
            ensure_system_path()

        parts = os.environ['PATH'].split(os.pathsep)
        assert r'C:\Program Files\nodejs' in parts

    @staticmethod
    @pytest.mark.skipif(os.name != 'nt', reason='Windows-only test')
    def test_case_insensitive_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing PATH entries should not be duplicated even with different case."""
        monkeypatch.setenv('PATH', r'c:\windows\system32')

        with patch(
            'porringer.core.path.read_registry_path',
            return_value=[r'C:\Windows\System32', r'C:\Program Files\nodejs'],
        ):
            ensure_system_path()

        parts = os.environ['PATH'].split(os.pathsep)
        # Only the new entry should appear; the existing one not duplicated
        system32_count = sum(1 for p in parts if p.lower() == r'c:\windows\system32')
        assert system32_count == 1
        assert r'C:\Program Files\nodejs' in parts

    @staticmethod
    @pytest.mark.skipif(os.name != 'nt', reason='Windows-only test')
    def test_registry_oserror_handled() -> None:
        """Gracefully handles inaccessible registry keys."""
        with patch('winreg.OpenKey', side_effect=OSError('access denied')):
            result = read_registry_path()

        assert result == []


# ---------------------------------------------------------------------------
# Unix: well-known directory probing
# ---------------------------------------------------------------------------


class TestUnixProbe:
    """Test the Unix well-known directory probing logic."""

    @staticmethod
    @pytest.mark.skipif(os.name == 'nt', reason='Unix-only test')
    def test_only_existing_dirs_returned() -> None:
        """Only directories that actually exist on disk are returned."""
        existing = {'/usr/local/bin'}
        with patch('os.path.isdir', side_effect=lambda d: d in existing):
            result = probe_unix_paths()
        assert '/usr/local/bin' in result
        # Non-existent dirs should be excluded
        for d in result:
            assert d in existing

    @staticmethod
    @pytest.mark.skipif(os.name == 'nt', reason='Unix-only test')
    def test_probe_merges_into_path(monkeypatch: pytest.MonkeyPatch) -> None:
        """Probed directories missing from PATH are appended."""
        monkeypatch.setenv('PATH', '/usr/bin')
        with patch(
            'porringer.core.path.probe_unix_paths',
            return_value=['/usr/local/bin', '/home/user/.local/bin'],
        ):
            ensure_system_path()

        parts = os.environ['PATH'].split(os.pathsep)
        assert '/usr/local/bin' in parts
        assert '/home/user/.local/bin' in parts

    @staticmethod
    @pytest.mark.skipif(os.name == 'nt', reason='Unix-only test')
    def test_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
        """Directories already in PATH are not added again."""
        monkeypatch.setenv('PATH', '/usr/local/bin:/usr/bin')
        with patch(
            'porringer.core.path.probe_unix_paths',
            return_value=['/usr/local/bin'],
        ):
            ensure_system_path()

        parts = os.environ['PATH'].split(os.pathsep)
        assert parts.count('/usr/local/bin') == 1


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Verify concurrent calls don't produce duplicate entries."""

    @staticmethod
    def test_concurrent_calls_safe(monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple threads calling ensure_system_path produce no duplicates."""
        monkeypatch.setenv('PATH', '/usr/bin')

        if os.name == 'nt':
            mock_target = 'porringer.core.path.read_registry_path'
            new_entry = r'C:\Program Files\nodejs'
        else:
            mock_target = 'porringer.core.path.probe_unix_paths'
            new_entry = '/opt/extra'

        with patch(mock_target, return_value=[new_entry]):
            barrier = threading.Barrier(4)
            errors: list[Exception] = []

            def worker():
                try:
                    barrier.wait(timeout=5)
                    ensure_system_path()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors
        parts = os.environ['PATH'].split(os.pathsep)
        count = sum(
            1
            for p in parts
            if (p.lower() if os.name == 'nt' else p) == (new_entry.lower() if os.name == 'nt' else new_entry)
        )
        assert count == 1
