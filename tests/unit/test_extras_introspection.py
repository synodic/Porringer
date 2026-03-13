"""Tests for extras introspection utilities in resolution.py.

Verifies that ``extras_satisfied()`` correctly evaluates PEP 508
conditional dependencies and that ``check_extras_installed()``
combines subprocess metadata collection with host-side evaluation.
"""

import json
from unittest.mock import AsyncMock, patch

from packaging.markers import default_environment
from packaging.utils import canonicalize_name

from porringer.backend.command.core.resolution import (
    check_extras_installed,
    extras_satisfied,
)

# ---------------------------------------------------------------------------
# Sample Requires-Dist data (matches real-world patterns)
# ---------------------------------------------------------------------------

_REQUESTS_REQUIRES = [
    'charset-normalizer<4,>=2',
    'idna<4,>=2.5',
    'urllib3<3,>=1.21.1',
    'certifi>=2017.4.17',
    'PySocks>=1.5.6 ; extra == "socks"',
    'pyOpenSSL>=0.14 ; extra == "security"',
    'cryptography>=1.3.4 ; extra == "security"',
    'win32-setctime>=1.0.0 ; extra == "win" and sys_platform == "win32"',
]


def _names(*packages: str) -> frozenset[str]:
    """Build a canonicalised installed-names set."""
    return frozenset(canonicalize_name(p) for p in packages)


# ---------------------------------------------------------------------------
# extras_satisfied
# ---------------------------------------------------------------------------


class TestExtrasSatisfied:
    """Unit tests for the marker-based extras check."""

    @staticmethod
    def test_all_deps_present() -> None:
        """Security extra is satisfied when both deps are installed."""
        installed = _names('pyOpenSSL', 'cryptography', 'requests', 'idna')
        assert extras_satisfied(_REQUESTS_REQUIRES, ('security',), installed) is True

    @staticmethod
    def test_missing_dep() -> None:
        """Security extra is unsatisfied when a dep is missing."""
        installed = _names('pyOpenSSL', 'requests')  # no cryptography
        assert extras_satisfied(_REQUESTS_REQUIRES, ('security',), installed) is False

    @staticmethod
    def test_empty_extras_always_satisfied() -> None:
        """No requested extras → always True."""
        assert extras_satisfied(_REQUESTS_REQUIRES, (), frozenset()) is True

    @staticmethod
    def test_empty_requires_always_satisfied() -> None:
        """Package with no requires → always True."""
        assert extras_satisfied([], ('security',), frozenset()) is True

    @staticmethod
    def test_multiple_extras() -> None:
        """Multiple extras checked: both satisfied."""
        installed = _names('PySocks', 'pyOpenSSL', 'cryptography')
        assert extras_satisfied(_REQUESTS_REQUIRES, ('socks', 'security'), installed) is True

    @staticmethod
    def test_multiple_extras_partial() -> None:
        """Multiple extras: one missing dep → False."""
        installed = _names('PySocks')  # security deps missing
        assert extras_satisfied(_REQUESTS_REQUIRES, ('socks', 'security'), installed) is False

    @staticmethod
    def test_compound_marker_respects_platform() -> None:
        """Compound markers that include platform checks are evaluated correctly.

        The ``win`` extra requires ``win32-setctime`` only on win32.
        Whether the dep is needed depends on the current platform.
        """
        is_win32 = default_environment()['sys_platform'] == 'win32'
        installed = _names()  # nothing installed
        result = extras_satisfied(_REQUESTS_REQUIRES, ('win',), installed)
        if is_win32:
            # On Windows the dep IS required → should fail
            assert result is False
        else:
            # On non-Windows the marker never activates → vacuously True
            assert result is True

    @staticmethod
    def test_name_normalisation() -> None:
        """Dep names are normalised before lookup (PEP 503)."""
        requires = ['Py-OpenSSL>=0.14 ; extra == "security"']
        installed = _names('py_openssl')  # underscore vs hyphen
        assert extras_satisfied(requires, ('security',), installed) is True

    @staticmethod
    def test_invalid_requirement_line_ignored() -> None:
        """Malformed Requires-Dist entries are silently skipped."""
        requires = [
            '!!!invalid!!!',
            'PySocks>=1.5.6 ; extra == "socks"',
        ]
        installed = _names('PySocks')
        assert extras_satisfied(requires, ('socks',), installed) is True


# ---------------------------------------------------------------------------
# check_extras_installed
# ---------------------------------------------------------------------------


class TestCheckExtrasInstalled:
    """Integration-ish tests for the subprocess + evaluation combo."""

    @staticmethod
    async def test_satisfied_returns_true() -> None:
        """When subprocess returns requires and deps present → True."""
        requires_json = json.dumps(_REQUESTS_REQUIRES).encode()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(requires_json, b''))

        installed = _names('pyOpenSSL', 'cryptography')

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await check_extras_installed('python', 'requests', ('security',), installed)
        assert result is True

    @staticmethod
    async def test_missing_dep_returns_false() -> None:
        """When subprocess returns requires but dep missing → False."""
        requires_json = json.dumps(_REQUESTS_REQUIRES).encode()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(requires_json, b''))

        installed = _names('pyOpenSSL')  # no cryptography

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await check_extras_installed('python', 'requests', ('security',), installed)
        assert result is False

    @staticmethod
    async def test_subprocess_failure_returns_none() -> None:
        """Subprocess non-zero exit → None."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error'))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await check_extras_installed('python', 'missing-pkg', ('security',), frozenset())
        assert result is None

    @staticmethod
    async def test_file_not_found_returns_none() -> None:
        """Python not on PATH → None."""
        with patch(
            'asyncio.create_subprocess_exec',
            side_effect=FileNotFoundError,
        ):
            result = await check_extras_installed('/no/such/python', 'requests', ('security',), frozenset())
        assert result is None

    @staticmethod
    async def test_timeout_returns_none() -> None:
        """Subprocess timeout → None."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await check_extras_installed('python', 'requests', ('security',), frozenset())
        assert result is None
