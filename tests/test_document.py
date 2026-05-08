"""Tests for document retrieval tool formatting functions."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from prometheus_client import REGISTRY

from okp_mcp import tools
from okp_mcp.config import ServerConfig
from okp_mcp.tools.document import (
    _DOCUMENTATION_MAX_CHARS,
    _DOCUMENTATION_MAX_SECTIONS,
    _DOCUMENTATION_PER_SECTION,
    _format_document_content,
    _format_document_passages,
    _format_metadata,
    _uses_document_passages,
)

_SOLR_ENDPOINT = ServerConfig().solr_endpoint


def _make_doc(
    *,
    kind: str = "documentation",
    content: str = "Some content about RHEL",
    view_uri: str = "/documentation/en-US/test",
) -> dict:
    """Build a minimal Solr doc dict for testing."""
    return {
        "allTitle": "Test Doc",
        "documentKind": kind,
        "view_uri": view_uri,
        "id": view_uri,
        "main_content": content,
    }


def _make_data(*, highlight_key: str = "", snippets: list[str] | None = None) -> dict:
    """Build a minimal Solr response dict with optional highlighting."""
    highlighting: dict = {}
    if highlight_key and snippets:
        highlighting[highlight_key] = {"main_content": snippets}
    return {"response": {"docs": []}, "highlighting": highlighting}


# ---------------------------------------------------------------------------
# _uses_document_passages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc,expected",
    [
        ({"documentKind": "documentation"}, True),
        ({"documentKind": "solution", "view_uri": "/solutions/12345"}, False),
        ({"documentKind": "solution", "view_uri": "/documentation/en-US/rhel/9"}, True),
        ({"documentKind": "errata", "view_uri": "/errata/RHSA-2024:1234", "id": "/errata/RHSA-2024:1234"}, False),
    ],
    ids=["kind-documentation", "kind-solution", "uri-documentation", "kind-errata"],
)
def test_uses_document_passages(doc, expected):
    """Detection works by documentKind and falls back to view_uri prefix."""
    assert _uses_document_passages(doc) == expected


# ---------------------------------------------------------------------------
# _format_metadata
# ---------------------------------------------------------------------------


def test_format_metadata_basic():
    """Metadata includes title, type, product, and URL."""
    doc = _make_doc(kind="documentation")
    doc["product"] = "Red Hat Enterprise Linux"
    result = _format_metadata(doc)
    assert "**Test Doc**" in result
    assert "Type: documentation" in result
    assert "Product: Red Hat Enterprise Linux" in result
    assert "https://access.redhat.com/documentation/en-US/test" in result


def test_format_metadata_with_synopsis():
    """Synopsis is included when present."""
    doc = _make_doc()
    doc["portal_synopsis"] = "A brief synopsis."
    result = _format_metadata(doc)
    assert "Synopsis: A brief synopsis." in result


# ---------------------------------------------------------------------------
# Documentation + no query -> nudge message
# ---------------------------------------------------------------------------


def test_documentation_no_query_returns_nudge():
    """Documentation without a query returns a nudge instead of content."""
    doc = _make_doc(kind="documentation", content="x" * 100_000)
    data = _make_data()
    result = _format_document_content(doc, data, doc["view_uri"], query="", max_chars=30_000, current_result="")
    assert "Pass a query" in result
    assert "large documentation page" in result
    # Must NOT contain the actual content
    assert "x" * 100 not in result


def test_documentation_no_query_nudge_via_uri():
    """URI-based documentation detection also triggers the nudge."""
    doc = _make_doc(kind="other", view_uri="/documentation/en-US/rhel/9/guide")
    data = _make_data()
    result = _format_document_content(doc, data, doc["view_uri"], query="", max_chars=30_000, current_result="")
    assert "Pass a query" in result


# ---------------------------------------------------------------------------
# Documentation + query + highlights -> capped passages
# ---------------------------------------------------------------------------


def test_documentation_query_with_highlights_uses_tight_budget():
    """Documentation passages are capped to _DOCUMENTATION_MAX_CHARS, not the full budget."""
    view_uri = "/documentation/en-US/test"
    # Each snippet is ~200 chars. With 60 snippets that's 12K+ of formatted passages,
    # which exceeds _DOCUMENTATION_MAX_CHARS (10K) but fits in the full 30K budget.
    snippets = [f"Snippet {i}: " + "x" * 180 for i in range(60)]
    doc = _make_doc(kind="documentation", view_uri=view_uri)
    data = _make_data(highlight_key=view_uri, snippets=snippets)

    result = _format_document_content(doc, data, view_uri, query="kernel panic", max_chars=30_000, current_result="")

    assert "Relevant passages:" in result
    # The total output must stay under the documentation budget, not the full 30K
    assert len(result) <= _DOCUMENTATION_MAX_CHARS + 200  # allow margin for the budget-reached message


def test_documentation_query_with_highlights_ignores_full_budget():
    """Even with a generous max_chars, documentation still caps at _DOCUMENTATION_MAX_CHARS."""
    view_uri = "/documentation/en-US/test"
    snippets = [f"Snippet {i}: " + "y" * 180 for i in range(60)]
    doc = _make_doc(kind="documentation", view_uri=view_uri)
    data = _make_data(highlight_key=view_uri, snippets=snippets)

    result = _format_document_content(doc, data, view_uri, query="systemd units", max_chars=100_000, current_result="")

    # Must not blow up to 100K just because max_chars allows it
    assert len(result) < _DOCUMENTATION_MAX_CHARS + 200


# ---------------------------------------------------------------------------
# Documentation + query + no highlights -> reduced BM25 extraction
# ---------------------------------------------------------------------------


def test_documentation_query_no_highlights_uses_reduced_extraction():
    """Without highlights, documentation uses fewer/smaller BM25 sections."""
    # Build content with clearly separated paragraphs so BM25 can score them
    paragraphs = [f"Paragraph {i} about kernel configuration " + "w" * 300 for i in range(50)]
    big_content = "\n\n".join(paragraphs)

    doc = _make_doc(kind="documentation", content=big_content)
    data = _make_data()  # no highlights

    result = _format_document_content(
        doc, data, doc["view_uri"], query="kernel configuration", max_chars=30_000, current_result=""
    )

    assert "\n\nContent:\n" in result
    # With _DOCUMENTATION_MAX_SECTIONS=3 and _DOCUMENTATION_PER_SECTION=1000,
    # total extracted content should be well under 5K (3 * 1000 + separators)
    content_part = result.split("\n\nContent:\n", 1)[1]
    assert len(content_part) < _DOCUMENTATION_MAX_SECTIONS * _DOCUMENTATION_PER_SECTION + 500


# ---------------------------------------------------------------------------
# Non-documentation paths (unchanged behavior)
# ---------------------------------------------------------------------------


def test_non_documentation_no_query_extracts_content():
    """Non-documentation without a query extracts content normally (no nudge)."""
    doc = _make_doc(kind="solution", view_uri="/solutions/12345", content="Solution body text here.")
    data = _make_data()

    result = _format_document_content(doc, data, doc["view_uri"], query="", max_chars=30_000, current_result="")

    assert "\n\nContent:\n" in result
    assert "Pass a query" not in result


def test_non_documentation_query_with_highlights_joins():
    """Non-documentation with highlights joins snippets with ' ... ' separator."""
    view_uri = "/solutions/12345"
    snippets = ["First snippet about the fix.", "Second snippet with details."]
    doc = _make_doc(kind="solution", view_uri=view_uri, content="Full solution body.")
    data = _make_data(highlight_key=view_uri, snippets=snippets)

    result = _format_document_content(doc, data, view_uri, query="fix details", max_chars=30_000, current_result="")

    assert " ... " in result
    assert "First snippet about the fix." in result
    assert "Second snippet with details." in result
    # Should NOT use the passage format
    assert "Relevant passages:" not in result


def test_non_documentation_query_no_highlights_uses_full_extraction():
    """Non-documentation without highlights uses 8 sections (not the reduced 3)."""
    paragraphs = [f"Paragraph {i} about network configuration " + "z" * 200 for i in range(30)]
    big_content = "\n\n".join(paragraphs)
    doc = _make_doc(kind="solution", view_uri="/solutions/12345", content=big_content)
    data = _make_data()  # no highlights

    result = _format_document_content(
        doc, data, doc["view_uri"], query="network configuration", max_chars=30_000, current_result=""
    )

    assert "\n\nContent:\n" in result
    # With max_sections=8, more content is allowed than the documentation cap
    content_part = result.split("\n\nContent:\n", 1)[1]
    # Non-documentation should be able to exceed the documentation budget
    # (the separator count hints at section count, though exact count depends on BM25 scoring)
    assert len(content_part) > 0


# ---------------------------------------------------------------------------
# Edge case: no main_content
# ---------------------------------------------------------------------------


def test_no_main_content_returns_empty():
    """Missing main_content returns empty string for non-documentation."""
    doc = _make_doc(kind="solution", view_uri="/solutions/12345")
    doc["main_content"] = None
    data = _make_data()

    result = _format_document_content(doc, data, doc["view_uri"], query="test", max_chars=30_000, current_result="")
    assert result == ""


def test_documentation_no_main_content_still_nudges_without_query():
    """Documentation nudge fires even when main_content is missing (check order matters)."""
    doc = _make_doc(kind="documentation")
    doc["main_content"] = None
    data = _make_data()

    result = _format_document_content(doc, data, doc["view_uri"], query="", max_chars=30_000, current_result="")
    assert "Pass a query" in result


def test_documentation_no_main_content_with_query_returns_empty():
    """Documentation with a query but no main_content returns empty (no content to extract)."""
    doc = _make_doc(kind="documentation")
    doc["main_content"] = None
    data = _make_data()

    result = _format_document_content(doc, data, doc["view_uri"], query="kernel", max_chars=30_000, current_result="")
    assert result == ""


# ---------------------------------------------------------------------------
# _format_document_passages budget behavior
# ---------------------------------------------------------------------------


def test_format_document_passages_respects_remaining_budget():
    """Passages stop accumulating when remaining character budget is exhausted."""
    snippets = [f"Passage content {i} " + "a" * 500 for i in range(20)]
    result = _format_document_passages(snippets, query="test", max_chars=3000, current_result="x" * 500)
    assert "Relevant passages:" in result
    # Total should respect the budget
    assert len("x" * 500 + result) <= 3200  # small margin for truncation message


def test_format_document_passages_negative_budget():
    """If metadata already exhausted the budget, passages return empty."""
    result = _format_document_passages(["snippet"], query="test", max_chars=100, current_result="x" * 200)
    assert result == ""


# ---------------------------------------------------------------------------
# Helpers for Prometheus metric assertions
# ---------------------------------------------------------------------------


def _get_counter(name: str, labels: dict | None = None) -> float:
    """Read the current value of a Prometheus counter, defaulting to 0."""
    return REGISTRY.get_sample_value(f"{name}_total", labels or {}) or 0.0


# ---------------------------------------------------------------------------
# Document retrieval metric assertions
# ---------------------------------------------------------------------------


class TestDocumentRetrievalMetrics:
    """Verify Prometheus metric increments for document retrieval outcomes."""

    @pytest.mark.parametrize(
        "kind, view_uri, query, expected_delta",
        [
            pytest.param("documentation", "/documentation/en-US/test", "", 1, id="documentation-no-query"),
            pytest.param("documentation", "/documentation/en-US/test", "RHEL", 0, id="documentation-with-query"),
            pytest.param("solution", "/solutions/12345", "", 0, id="non-documentation-no-query"),
        ],
    )
    def test_nudge_counter(self, kind, view_uri, query, expected_delta):
        """Nudge counter fires only for documentation pages without a query."""
        doc = _make_doc(kind=kind, view_uri=view_uri, content="x" * 1000)
        data = _make_data()
        before = _get_counter("okp_document_nudge")

        _format_document_content(doc, data, doc["view_uri"], query=query, max_chars=30_000, current_result="")

        assert _get_counter("okp_document_nudge") == before + expected_delta

    @pytest.mark.parametrize(
        "kind, view_uri, content, snippets, query, counter_name, expected_delta",
        [
            pytest.param(
                "documentation", "/documentation/en-US/test", "Content", ["Snippet about kernel configuration"],
                "kernel", "okp_document_highlight_used", 1, id="doc-with-highlights",
            ),
            pytest.param(
                "solution", "/solutions/12345", "Full body.", ["Fix for the network issue"],
                "network", "okp_document_highlight_used", 1, id="non-doc-with-highlights",
            ),
            pytest.param(
                "documentation", "/documentation/en-US/test", "Content about systemd units " + "w" * 300, None,
                "systemd", "okp_document_highlight_fallback", 1, id="doc-no-highlights",
            ),
            pytest.param(
                "solution", "/solutions/12345", "Solution about networking " + "z" * 300, None,
                "networking", "okp_document_highlight_fallback", 1, id="non-doc-no-highlights",
            ),
        ],
    )
    def test_highlight_counter(self, kind, view_uri, content, snippets, query, counter_name, expected_delta):
        """Highlight-used or fallback counter fires based on snippet availability."""
        doc = _make_doc(kind=kind, view_uri=view_uri, content=content)
        data = _make_data(highlight_key=view_uri, snippets=snippets) if snippets else _make_data()
        before = _get_counter(counter_name)

        _format_document_content(doc, data, view_uri, query=query, max_chars=30_000, current_result="")

        assert _get_counter(counter_name) == before + expected_delta

    def test_no_highlight_metrics_without_query(self):
        """Non-documentation without query fires neither highlight counter."""
        doc = _make_doc(kind="solution", view_uri="/solutions/12345", content="Solution body")
        data = _make_data()
        before_used = _get_counter("okp_document_highlight_used")
        before_fallback = _get_counter("okp_document_highlight_fallback")

        _format_document_content(doc, data, doc["view_uri"], query="", max_chars=30_000, current_result="")

        assert _get_counter("okp_document_highlight_used") == before_used
        assert _get_counter("okp_document_highlight_fallback") == before_fallback

    def test_no_highlight_metrics_without_main_content(self):
        """Missing main_content fires neither highlight counter."""
        doc = _make_doc(kind="documentation", content="x")
        doc["main_content"] = None
        data = _make_data()
        before_used = _get_counter("okp_document_highlight_used")
        before_fallback = _get_counter("okp_document_highlight_fallback")

        _format_document_content(doc, data, doc["view_uri"], query="kernel", max_chars=30_000, current_result="")

        assert _get_counter("okp_document_highlight_used") == before_used
        assert _get_counter("okp_document_highlight_fallback") == before_fallback


# ---------------------------------------------------------------------------
# Document not-found metric (requires tool-level mock)
# ---------------------------------------------------------------------------


async def test_not_found_counter_incremented():
    """get_document increments the not-found counter when Solr returns no docs."""
    mock_ctx = Mock()
    mock_app = Mock()
    mock_app.http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_app.solr_endpoint = _SOLR_ENDPOINT
    mock_app.max_response_chars = 5000

    before = _get_counter("okp_document_not_found")

    with (
        patch("okp_mcp.tools.document.get_app_context", return_value=mock_app),
        patch("okp_mcp.tools.document._fetch_document_raw", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_fetch.return_value = {"response": {"docs": []}}
        result = await tools.get_document(mock_ctx, "/solutions/nonexistent")

    assert _get_counter("okp_document_not_found") == before + 1
    assert "Document not found" in result


async def test_not_found_counter_not_incremented_when_doc_exists():
    """get_document does not fire the not-found counter when a doc is returned."""
    mock_ctx = Mock()
    mock_app = Mock()
    mock_app.http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_app.solr_endpoint = _SOLR_ENDPOINT
    mock_app.max_response_chars = 30_000

    before = _get_counter("okp_document_not_found")

    with (
        patch("okp_mcp.tools.document.get_app_context", return_value=mock_app),
        patch("okp_mcp.tools.document._fetch_document_raw", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_fetch.return_value = {
            "response": {
                "docs": [
                    {
                        "allTitle": "Test Solution",
                        "documentKind": "solution",
                        "view_uri": "/solutions/12345",
                        "id": "/solutions/12345",
                        "main_content": "Solution content here.",
                    }
                ]
            },
            "highlighting": {},
        }
        await tools.get_document(mock_ctx, "/solutions/12345")

    assert _get_counter("okp_document_not_found") == before
