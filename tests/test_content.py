"""Tests for okp_mcp.content module."""

import pytest

from okp_mcp.content import clean_content, strip_boilerplate, strip_index_suffix, truncate_content


@pytest.mark.parametrize(
    "text,max_chars",
    [
        ("This is a short text.", 100),
        ("Exact.", 6),
    ],
    ids=["under-limit", "exact-limit"],
)
def test_truncate_content_no_op(text, max_chars):
    """Text at or under max_chars is returned unchanged."""
    assert truncate_content(text, max_chars=max_chars) == text


def test_truncate_content_over_limit():
    """Truncated text includes character counts in the truncation message."""
    result = truncate_content("x" * 100, max_chars=50)
    assert result.startswith("x" * 50)
    assert "[Content truncated - showing 50 of 100 characters]" in result


@pytest.mark.parametrize(
    "text,forbidden",
    [
        (
            "Main content.\n\nThis solution is part of Red Hat's fast-track publication program. Extra.",
            "fast-track publication program",
        ),
        (
            "Before. This content is not included. After.",
            "This content is not included.",
        ),
        (
            "A. This content is not included. B. This content is not included. C.",
            "This content is not included.",
        ),
    ],
    ids=["fast-track-footer", "not-included-marker", "multiple-occurrences"],
)
def test_strip_boilerplate_removes_patterns(text, forbidden):
    """Known boilerplate patterns are stripped from text."""
    assert forbidden not in strip_boilerplate(text)


def test_strip_boilerplate_preserves_clean_text():
    """Text without boilerplate passes through unchanged."""
    text = "Clean content with no boilerplate markers."
    assert strip_boilerplate(text) == text


@pytest.mark.parametrize(
    "text,max_chars,expected",
    [
        (None, 100, ""),
        ("", 100, ""),
        ("Normal text without boilerplate.", 1000, "Normal text without boilerplate."),
    ],
    ids=["none-input", "empty-string", "clean-passthrough"],
)
def test_clean_content_edge_cases(text, max_chars, expected):
    """Edge cases: None, empty string, and clean text pass through correctly."""
    assert clean_content(text, max_chars=max_chars) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/solutions/3257611/index.html", "/solutions/3257611"),
        ("/articles/2585/index.html", "/articles/2585"),
        ("/documentation/en-us/rhel/9/html-single/guide/index.html", "/documentation/en-us/rhel/9/html-single/guide"),
        ("/security/cve/CVE-2024-9823/", "/security/cve/CVE-2024-9823/"),
        ("/errata/RHSA-2022:4915/", "/errata/RHSA-2022:4915/"),
        ("", ""),
        ("/solutions/123", "/solutions/123"),
    ],
    ids=[
        "solution-id",
        "article-id",
        "documentation-id",
        "cve-view-uri-unchanged",
        "errata-view-uri-unchanged",
        "empty-string",
        "no-suffix",
    ],
)
def test_strip_index_suffix(path, expected):
    """Trailing /index.html is stripped from Solr document ID paths."""
    assert strip_index_suffix(path) == expected


def test_clean_content_strips_then_truncates():
    """Both boilerplate patterns are stripped before truncation is applied."""
    text = (
        "Useful. " * 50
        + "This content is not included. "
        + "More useful. " * 50
        + "This solution is part of Red Hat's fast-track publication program."
    )
    result = clean_content(text, max_chars=300)
    assert "This content is not included." not in result
    assert "fast-track publication program" not in result
