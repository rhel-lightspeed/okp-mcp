"""Tests for okp_mcp.content module."""

import pytest

from okp_mcp.content import _select_within_budget, clean_content, doc_uri, strip_boilerplate, truncate_content


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
    "doc,expected",
    [
        ({"id": "/solutions/3257611/index.html"}, "/solutions/3257611"),
        ({"id": "/articles/2585/index.html"}, "/articles/2585"),
        (
            {"id": "/documentation/en-us/rhel/9/html-single/guide/index.html"},
            "/documentation/en-us/rhel/9/html-single/guide",
        ),
        ({"view_uri": "/security/cve/CVE-2024-9823/"}, "/security/cve/CVE-2024-9823/"),
        ({"view_uri": "/errata/RHSA-2022:4915/"}, "/errata/RHSA-2022:4915/"),
        ({}, ""),
        ({"id": "/solutions/123"}, "/solutions/123"),
        ({"view_uri": "/solutions/7134031", "id": "/solutions/7134031/index.html"}, "/solutions/7134031"),
    ],
    ids=[
        "solution-id-strips-suffix",
        "article-id-strips-suffix",
        "documentation-id-strips-suffix",
        "cve-view-uri-unchanged",
        "errata-view-uri-unchanged",
        "empty-doc-returns-empty",
        "no-suffix-unchanged",
        "view-uri-preferred-over-id",
    ],
)
def test_doc_uri(doc, expected):
    """doc_uri returns canonical URL path, preferring view_uri and stripping /index.html."""
    assert doc_uri(doc) == expected


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


# --- _select_within_budget tests ---


def test_select_within_budget_all_fit():
    """Results fitting within budget are all included without a truncation notice."""
    results = ["short1", "short2", "short3"]
    output = _select_within_budget(results, max_chars=1000, query="test")
    assert "short1" in output
    assert "short2" in output
    assert "short3" in output
    assert "Budget reached" not in output


def test_select_within_budget_drops_tail():
    """Results exceeding the budget are dropped with a count message."""
    big = "x" * 2000
    results = [big] * 5
    output = _select_within_budget(results, max_chars=5000, query="test")
    assert "Budget reached" in output
    assert "of 5 results" in output


def test_select_within_budget_single_huge_truncated():
    """A single result exceeding the budget is hard-truncated via truncate_content."""
    huge = "y" * 50_000
    output = _select_within_budget([huge], max_chars=1000, query="test")
    assert len(output) <= 1200  # slack for the truncation message itself
    assert "Content truncated" in output


def test_select_within_budget_empty():
    """Empty results list returns a no-results message containing the query."""
    output = _select_within_budget([], max_chars=30_000, query="myquery")
    assert "No results found for: myquery" in output


def test_select_within_budget_separator():
    """Included results are joined with the expected separator."""
    results = ["result_a", "result_b"]
    output = _select_within_budget(results, max_chars=10_000, query="test")
    assert "---" in output
    assert "result_a" in output
    assert "result_b" in output


def test_select_within_budget_exact_boundary():
    """A single result exactly at the budget boundary is included without truncation."""
    result = "a" * 100
    output = _select_within_budget([result], max_chars=100, query="test")
    assert output == result


def test_select_within_budget_tiny_budget():
    """A very small budget causes even the first result to be truncated."""
    result = "a" * 1000
    output = _select_within_budget([result], max_chars=10, query="test")
    assert "Content truncated" in output


def test_select_within_budget_first_exceeds_budget_multi():
    """First result in a multi-result list exceeding the budget is hard-truncated."""
    big = "z" * 5000
    output = _select_within_budget([big, "small"], max_chars=100, query="test")
    assert "Content truncated" in output
    assert len(output) <= 200
    assert "Budget reached" not in output
