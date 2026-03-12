"""Tests for python_command() behaviour in frozen (PyInstaller) applications.

When ``sys.frozen`` is ``True``, ``sys.executable`` points to the
packaged binary (e.g. ``synodic.exe``) rather than a Python
interpreter.  ``python_command()`` must detect this and fall back to
``shutil.which('python')`` instead of blindly using ``sys.executable``.
"""

import sys
from unittest.mock import patch

from porringer.core.plugin_schema.runtime import RuntimeContext
from tests.conftest import frozen_context
from tests.fixtures.mock_plugins import MOCK_DIST, MOCK_RUNTIME_EXE, MockPythonEnv

_SYSTEM_PYTHON = r'C:\Python314\python.exe'
_FROZEN_EXE = r'C:\app\synodic.exe'  # matches frozen_context default


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPythonCommandFrozenApp:
    """python_command() must handle sys.frozen gracefully."""

    @staticmethod
    def test_frozen_no_runtime_uses_which() -> None:
        """In a frozen app with no runtime override, prefer shutil.which('python')."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        with frozen_context(which_result=_SYSTEM_PYTHON):
            result = env.python_command(empty_ctx)

        assert result == _SYSTEM_PYTHON

    @staticmethod
    def test_frozen_with_runtime_context_uses_override() -> None:
        """Runtime context override takes priority even in frozen apps."""
        env = MockPythonEnv(MOCK_DIST)
        rc = RuntimeContext(executables={'python': MOCK_RUNTIME_EXE})

        with frozen_context():
            result = env.python_command(rc)

        assert result == str(MOCK_RUNTIME_EXE)

    @staticmethod
    def test_frozen_no_which_falls_back_to_sys_executable() -> None:
        """When shutil.which also fails, gracefully degrade to sys.executable."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch.object(sys, 'executable', _FROZEN_EXE),
            patch('porringer.core.plugin_schema.python_environment.shutil.which', return_value=None),
        ):
            result = env.python_command(empty_ctx)

        assert result == _FROZEN_EXE

    @staticmethod
    def test_not_frozen_ignores_which() -> None:
        """When not frozen, python_command() returns sys.executable as before."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        original_exe = sys.executable
        # Ensure sys.frozen is absent (the normal case)
        with (
            patch('porringer.core.plugin_schema.python_environment.shutil.which', return_value=_SYSTEM_PYTHON),
        ):
            # Remove sys.frozen if it somehow exists
            frozen = getattr(sys, 'frozen', None)
            if frozen is not None:
                delattr(sys, 'frozen')
            try:
                result = env.python_command(empty_ctx)
            finally:
                if frozen is not None:
                    sys.frozen = frozen  # type: ignore[attr-defined]

        assert result == original_exe

    @staticmethod
    def test_frozen_none_runtime_context_uses_which() -> None:
        """With runtime_context=None (not just empty), still applies frozen fallback."""
        env = MockPythonEnv(MOCK_DIST)

        with frozen_context(which_result=_SYSTEM_PYTHON):
            result = env.python_command(None)

        assert result == _SYSTEM_PYTHON
