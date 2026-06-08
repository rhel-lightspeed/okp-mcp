"""Tests for build_info module."""

from unittest.mock import patch

from okp_mcp.build_info import get_commit_sha, get_package_version


def test_get_commit_sha_reads_env():
    """Return the trimmed value of the COMMIT_SHA environment variable."""
    with patch.dict("os.environ", {"COMMIT_SHA": "abc1234\n"}):
        assert get_commit_sha() == "abc1234"


def test_get_commit_sha_fallback_when_unset():
    """Fall back to 'development' when COMMIT_SHA is not set."""
    with patch.dict("os.environ", {}, clear=True):
        assert get_commit_sha() == "development"


def test_get_commit_sha_fallback_when_empty():
    """Fall back to 'development' when COMMIT_SHA is set but blank."""
    with patch.dict("os.environ", {"COMMIT_SHA": "  "}):
        assert get_commit_sha() == "development"


def test_get_package_version():
    """Return the installed package version string."""
    result = get_package_version()
    # The package is installed in dev mode, so version should be available
    assert isinstance(result, str)
    assert len(result) > 0
