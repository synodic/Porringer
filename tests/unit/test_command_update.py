"""Test the update command"""

import tempfile
from logging import Logger
from pathlib import Path

import pytest
from packaging.version import Version
from typer.testing import CliRunner

from porringer.api import API
from porringer.console.entry import app
from porringer.schema import (
    APIParameters,
    CheckUpdateParameters,
    DownloadParameters,
    HashAlgorithm,
    LocalConfiguration,
    UpdateInfo,
    UpdateSource,
)
from porringer.utility.download import compute_file_hash, parse_hash_string
from porringer.utility.exception import UpdateError


class TestUpdateCheck:
    """Tests for update checking"""

    @staticmethod
    def test_check_parameters_validation() -> None:
        """Test that CheckUpdateParameters validates correctly"""
        params = CheckUpdateParameters(
            source=UpdateSource.GITHUB_RELEASES,
            current_version='1.0.0',
            repo='owner/repo',
        )
        assert params.source == UpdateSource.GITHUB_RELEASES
        assert params.current_version == '1.0.0'
        assert params.repo == 'owner/repo'
        assert params.include_prereleases is False

    @staticmethod
    def test_check_github_requires_repo() -> None:
        """Test that GitHub source requires repo parameter"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = CheckUpdateParameters(
            source=UpdateSource.GITHUB_RELEASES,
            current_version='1.0.0',
        )

        with pytest.raises(UpdateError, match='repo'):
            api.update.check(params)

    @staticmethod
    def test_check_pypi_requires_package() -> None:
        """Test that PyPI source requires package parameter"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = CheckUpdateParameters(
            source=UpdateSource.PYPI,
            current_version='1.0.0',
        )

        with pytest.raises(UpdateError, match='package'):
            api.update.check(params)

    @staticmethod
    def test_check_custom_requires_url() -> None:
        """Test that custom source requires url parameter"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = CheckUpdateParameters(
            source=UpdateSource.CUSTOM_URL,
            current_version='1.0.0',
        )

        with pytest.raises(UpdateError, match='url'):
            api.update.check(params)


class TestUpdateInfo:
    """Tests for UpdateInfo dataclass"""

    @staticmethod
    def test_update_info_available() -> None:
        """Test UpdateInfo when update is available"""
        info = UpdateInfo(
            available=True,
            current_version=Version('1.0.0'),
            latest_version=Version('2.0.0'),
            download_url='https://example.com/v2.0.0.zip',
        )
        assert info.available is True
        assert info.current_version == Version('1.0.0')
        assert info.latest_version == Version('2.0.0')

    @staticmethod
    def test_update_info_not_available() -> None:
        """Test UpdateInfo when no update is available"""
        info = UpdateInfo(
            available=False,
            current_version=Version('2.0.0'),
        )
        assert info.available is False
        assert info.latest_version is None


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

    @staticmethod
    def test_download_parameters_defaults() -> None:
        """Test DownloadParameters default values"""
        params = DownloadParameters(
            url='https://example.com/file.zip',
            destination=Path('/tmp/file.zip'),
        )
        assert params.expected_hash is None
        assert params.expected_size is None
        # Verify against field defaults
        timeout_default = DownloadParameters.model_fields['timeout'].default
        chunk_size_default = DownloadParameters.model_fields['chunk_size'].default
        assert params.timeout == timeout_default
        assert params.chunk_size == chunk_size_default


class TestUpdateCLI:
    """Tests for update CLI commands"""

    @staticmethod
    def test_update_check_invalid_source(test_config) -> None:
        """Test that invalid source is rejected"""
        runner = CliRunner()

        result = runner.invoke(
            app,
            ['update', 'check', '--source', 'invalid', '--current', '1.0.0'],
            obj=test_config,
        )

        assert result.exit_code == 1

    @staticmethod
    def test_update_check_github_missing_repo(test_config) -> None:
        """Test that GitHub source without repo shows error"""
        runner = CliRunner()

        result = runner.invoke(
            app,
            ['update', 'check', '--source', 'github', '--current', '1.0.0'],
            obj=test_config,
        )

        assert result.exit_code == 1

    @staticmethod
    def test_update_check_pypi_missing_package(test_config) -> None:
        """Test that PyPI source without package shows error"""
        runner = CliRunner()

        result = runner.invoke(
            app,
            ['update', 'check', '--source', 'pypi', '--current', '1.0.0'],
            obj=test_config,
        )

        assert result.exit_code == 1


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
