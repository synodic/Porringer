"""Unit tests for Git SCM clone detection and URL comparison."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.schema import Distribution, PluginParameters
from porringer.plugin.git.plugin import GitScm
from porringer.schema.execution import CloneStatusKind

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


# -- urls_match tests -------------------------------------------------


class TestUrlsMatch:
    """Tests for ScmEnvironment.urls_match."""

    @staticmethod
    @pytest.mark.parametrize(
        ('a', 'b'),
        [
            ('https://github.com/org/repo', 'https://github.com/org/repo'),
            ('https://github.com/org/repo.git', 'https://github.com/org/repo'),
            ('https://github.com/org/repo', 'https://github.com/org/repo.git'),
            ('https://github.com/org/repo.git', 'https://github.com/org/repo.git'),
            ('https://github.com/org/repo/', 'https://github.com/org/repo'),
            ('https://github.com/org/repo.git/', 'https://github.com/org/repo'),
        ],
    )
    def test_equivalent_urls(a: str, b: str) -> None:
        """URLs that differ only by trailing .git or / should match."""
        assert ScmEnvironment.urls_match(a, b) is True

    @staticmethod
    @pytest.mark.parametrize(
        ('a', 'b'),
        [
            ('https://github.com/org/repo', 'https://github.com/other/repo'),
            ('https://github.com/org/repo', 'https://gitlab.com/org/repo'),
            ('https://github.com/org/repo', 'http://github.com/org/repo'),
            ('https://github.com/org/repo', 'https://github.com/org/different'),
        ],
    )
    def test_different_urls(a: str, b: str) -> None:
        """URLs with different host, scheme, or path should not match."""
        assert ScmEnvironment.urls_match(a, b) is False


# -- GitScm.get_remote_url tests --------------------------------------


class TestGetRemoteUrl:
    """Tests for GitScm.get_remote_url."""

    @staticmethod
    def test_returns_url_on_success(tmp_path: Path) -> None:
        """get_remote_url returns the stripped stdout on success."""
        scm = GitScm(_PARAMS)
        expected = 'https://github.com/org/repo.git'
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=f'  {expected}  \n', stderr='')
        with patch('subprocess.run', return_value=fake_result) as mock_run:
            result = scm.get_remote_url(tmp_path)
            mock_run.assert_called_once()
        assert result == expected

    @staticmethod
    def test_returns_none_on_failure(tmp_path: Path) -> None:
        """get_remote_url returns None when git exits non-zero."""
        scm = GitScm(_PARAMS)
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='error')
        with patch('subprocess.run', return_value=fake_result):
            result = scm.get_remote_url(tmp_path)
        assert result is None

    @staticmethod
    def test_returns_none_on_missing_git(tmp_path: Path) -> None:
        """get_remote_url returns None when git is not found."""
        scm = GitScm(_PARAMS)
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = scm.get_remote_url(tmp_path)
        assert result is None


# -- GitScm.is_cloned tests -------------------------------------------


class TestIsCloned:
    """Tests for GitScm.is_cloned."""

    @staticmethod
    def test_missing_when_no_directory(tmp_path: Path) -> None:
        """is_cloned returns MISSING when destination does not exist."""
        scm = GitScm(_PARAMS)
        result = scm.is_cloned('https://github.com/org/repo', tmp_path / 'nonexistent')
        assert result.kind == CloneStatusKind.MISSING
        assert result.remote_url is None

    @staticmethod
    def test_missing_when_no_git_dir(tmp_path: Path) -> None:
        """is_cloned returns MISSING when .git subdirectory is absent."""
        scm = GitScm(_PARAMS)
        result = scm.is_cloned('https://github.com/org/repo', tmp_path)
        assert result.kind == CloneStatusKind.MISSING

    @staticmethod
    def test_cloned_when_url_matches(tmp_path: Path) -> None:
        """is_cloned returns CLONED when remote URL matches (with .git suffix normalization)."""
        (tmp_path / '.git').mkdir()
        scm = GitScm(_PARAMS)
        url = 'https://github.com/org/repo'
        remote = 'https://github.com/org/repo.git'
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=f'{remote}\n', stderr='')
        with patch('subprocess.run', return_value=fake_result):
            result = scm.is_cloned(url, tmp_path)
        assert result.kind == CloneStatusKind.CLONED
        assert result.remote_url == remote

    @staticmethod
    def test_url_mismatch_when_different_remote(tmp_path: Path) -> None:
        """is_cloned returns URL_MISMATCH when remote URL differs."""
        (tmp_path / '.git').mkdir()
        scm = GitScm(_PARAMS)
        url = 'https://github.com/org/repo'
        remote = 'https://github.com/other/different'
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=f'{remote}\n', stderr='')
        with patch('subprocess.run', return_value=fake_result):
            result = scm.is_cloned(url, tmp_path)
        assert result.kind == CloneStatusKind.URL_MISMATCH
        assert result.remote_url == remote

    @staticmethod
    def test_url_mismatch_when_get_remote_fails(tmp_path: Path) -> None:
        """is_cloned returns URL_MISMATCH when git remote query fails."""
        (tmp_path / '.git').mkdir()
        scm = GitScm(_PARAMS)
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='error')
        with patch('subprocess.run', return_value=fake_result):
            result = scm.is_cloned('https://github.com/org/repo', tmp_path)
        assert result.kind == CloneStatusKind.URL_MISMATCH
        assert result.remote_url is None
