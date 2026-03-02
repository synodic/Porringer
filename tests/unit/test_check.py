"""Test the check and download functionality"""

import tempfile
from pathlib import Path

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.environment import CheckUpdatesParameters
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PackageRef
from porringer.schema import (
    CheckResult,
    DownloadParameters,
    HashAlgorithm,
    PackageUpdateInfo,
)
from porringer.utility.download import compute_file_hash, parse_hash_string


class TestCheckUpdatesParametersRuntimeContext:
    """Verify CheckUpdatesParameters carries runtime_context."""

    @staticmethod
    def test_defaults_to_none() -> None:
        """runtime_context defaults to None."""
        params = CheckUpdatesParameters(packages=[])
        assert params.runtime_context is None

    @staticmethod
    def test_accepts_runtime_context() -> None:
        """runtime_context can be passed and retrieved."""
        ctx = RuntimeContext()
        ctx.executables['python'] = '/usr/bin/python3'
        params = CheckUpdatesParameters(packages=[], runtime_context=ctx)
        assert params.runtime_context is ctx

    @staticmethod
    def test_excluded_from_serialization() -> None:
        """runtime_context is excluded from dict serialization."""
        ctx = RuntimeContext()
        params = CheckUpdatesParameters(
            packages=[PackageRef.model_validate('ruff')],
            runtime_context=ctx,
        )
        d = params.model_dump()
        assert 'runtime_context' not in d


class TestCheckResult:
    """Tests for CheckResult dataclass"""

    @staticmethod
    def test_check_result_success() -> None:
        """Test CheckResult with successful check"""
        result = CheckResult(
            plugin='pip',
            packages=[
                PackageUpdateInfo(
                    name='requests',
                    current_version=Version('2.28.0'),
                    latest_version=Version('2.31.0'),
                    update_available=True,
                )
            ],
        )
        assert result.success is True
        assert result.updates_available == 1
        assert result.error is None

    @staticmethod
    def test_check_result_no_updates() -> None:
        """Test CheckResult with no updates"""
        result = CheckResult(plugin='pip', packages=[])
        assert result.success is True
        assert result.updates_available == 0

    @staticmethod
    def test_check_result_error() -> None:
        """Test CheckResult with error"""
        result = CheckResult(plugin='pip', error='Connection failed')
        assert result.success is False
        assert result.updates_available == 0


class TestDownloadParameters:
    """Tests for download parameters"""

    @staticmethod
    def test_download_parameters_validation() -> None:
        """Test that DownloadParameters validates correctly"""
        params = DownloadParameters(
            url='https://example.com/file.zip',
            destination=Path('/tmp/file.zip'),
            expected_hash='sha256:abc123',
        )
        assert params.url == 'https://example.com/file.zip'
        assert params.expected_hash == 'sha256:abc123'
        # Verify default timeout value
        assert params.timeout == DownloadParameters.model_fields['timeout'].default


class TestDownloadUtility:
    """Tests for download utility functions"""

    @staticmethod
    def test_parse_hash_string() -> None:
        """Test parsing hash strings"""
        algo, digest = parse_hash_string('sha256:abc123def456')
        assert algo == HashAlgorithm.SHA256
        assert digest == 'abc123def456'

    @staticmethod
    def test_parse_hash_string_sha512() -> None:
        """Test parsing SHA512 hash strings"""
        algo, digest = parse_hash_string('sha512:abc123def456')
        assert algo == HashAlgorithm.SHA512
        assert digest == 'abc123def456'

    @staticmethod
    def test_parse_hash_string_invalid() -> None:
        """Test that invalid hash format raises error"""
        with pytest.raises(ValueError, match='Invalid hash format'):
            parse_hash_string('invalid-hash-string')

    @staticmethod
    def test_compute_file_hash() -> None:
        """Test computing file hash"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / 'test.txt'
            test_file.write_bytes(b'test content')

            hash_value = compute_file_hash(test_file, HashAlgorithm.SHA256)
            # SHA256 of "test content"
            assert hash_value == '6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72'
