"""Tests for build_info module."""

from unittest.mock import patch

from okp_mcp.build_info import _commit_sha_from_git
from okp_mcp.build_info import get_commit_sha
from okp_mcp.build_info import get_package_version


def test_get_commit_sha_reads_file(tmp_path):
    """Return the trimmed value from the baked COMMIT_SHA file."""
    (tmp_path / "COMMIT_SHA").write_text("def5678\n", encoding="utf-8")

    with patch("okp_mcp.build_info.DEFAULT_APP_ROOT", str(tmp_path)):
        assert get_commit_sha() == "def5678"


def test_get_commit_sha_uses_git_when_file_missing(tmp_path):
    """Fall back to local git when the baked file is absent."""
    with (
        patch("okp_mcp.build_info.DEFAULT_APP_ROOT", str(tmp_path)),
        patch("okp_mcp.build_info._commit_sha_from_git", return_value="9abcdef"),
    ):
        assert get_commit_sha() == "9abcdef"


def test_get_commit_sha_falls_back_to_default_when_git_unavailable(tmp_path):
    """Return the default sentinel when the file is absent and git is unavailable."""
    with (
        patch("okp_mcp.build_info.DEFAULT_APP_ROOT", str(tmp_path)),
        patch("okp_mcp.build_info._commit_sha_from_git", return_value=None),
        patch("okp_mcp.build_info.DEFAULT_COMMIT_SHA", "development"),
    ):
        assert get_commit_sha() == "development"


def test_commit_sha_from_git_returns_none_when_git_missing():
    """Return None when the git executable is unavailable."""
    with patch("okp_mcp.build_info.subprocess.run", side_effect=FileNotFoundError):
        assert _commit_sha_from_git() is None


def test_get_package_version():
    """Return the installed package version string."""
    result = get_package_version()
    # The package is installed in dev mode, so version should be available
    assert isinstance(result, str)
    assert len(result) > 0
