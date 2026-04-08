"""Tests for build_info module."""

from unittest.mock import mock_open, patch

from okp_mcp.build_info import get_commit_sha, get_package_version


def test_get_commit_sha_reads_file():
    """Return the trimmed contents of the COMMIT_SHA file."""
    with patch("builtins.open", mock_open(read_data="abc1234\n")):
        assert get_commit_sha() == "abc1234"


def test_get_commit_sha_fallback_when_missing():
    """Fall back to 'development' when COMMIT_SHA file does not exist."""
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert get_commit_sha() == "development"


def test_get_commit_sha_fallback_on_permission_error():
    """Fall back to 'development' on PermissionError or other OSError."""
    with patch("builtins.open", side_effect=PermissionError):
        assert get_commit_sha() == "development"


def test_get_commit_sha_fallback_on_empty_file():
    """Fall back to 'development' when the file exists but is empty."""
    with patch("builtins.open", mock_open(read_data="  \n")):
        assert get_commit_sha() == "development"


def test_get_package_version():
    """Return the installed package version string."""
    result = get_package_version()
    # The package is installed in dev mode, so version should be available
    assert isinstance(result, str)
    assert len(result) > 0
