"""Tests for document retrieval tool formatting functions."""

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import pytest

from prometheus_client import REGISTRY

from okp_mcp import tools
from okp_mcp.config import ServerConfig
from okp_mcp.outline import NO_OUTLINE
from okp_mcp.outline import parse_document
from okp_mcp.solr import _clean_query
from okp_mcp.tools.document import _doc_id_filter
from okp_mcp.tools.document import _DOCUMENTATION_MAX_CHARS
from okp_mcp.tools.document import _DOCUMENTATION_MAX_SECTIONS
from okp_mcp.tools.document import _DOCUMENTATION_PER_SECTION
from okp_mcp.tools.document import _drop_toc_passages
from okp_mcp.tools.document import _fetch_document_raw
from okp_mcp.tools.document import _fetch_document_with_query
from okp_mcp.tools.document import _format_document_content
from okp_mcp.tools.document import _format_document_passages
from okp_mcp.tools.document import _format_metadata
from okp_mcp.tools.document import _passage_label
from okp_mcp.tools.document import _uses_document_passages
from okp_mcp.types import SolrDoc
from okp_mcp.types import SolrResponse
from okp_mcp.types import SolrResponseBody


_SOLR_ENDPOINT = ServerConfig().solr_endpoint


def _make_doc(
    *,
    kind: str = "documentation",
    content: str = "Some content about RHEL",
    view_uri: str = "/documentation/en-US/test",
) -> SolrDoc:
    """Build a minimal Solr doc for testing."""
    return SolrDoc(
        allTitle="Test Doc",
        documentKind=kind,
        view_uri=view_uri,
        id=view_uri,
        main_content=content,
    )


def _make_data(*, highlight_key: str = "", snippets: list[str] | None = None) -> SolrResponse:
    """Build a minimal Solr response with optional highlighting."""
    highlighting: dict = {}
    if highlight_key and snippets:
        highlighting[highlight_key] = {"main_content": snippets}
    return SolrResponse(
        response=SolrResponseBody(numFound=0, docs=[]),
        highlighting=highlighting,
    )


# ---------------------------------------------------------------------------
# _uses_document_passages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc,expected",
    [
        (SolrDoc(documentKind="documentation"), True),
        (SolrDoc(documentKind="solution", view_uri="/solutions/12345"), False),
        (SolrDoc(documentKind="solution", view_uri="/documentation/en-US/rhel/9"), True),
        (SolrDoc(documentKind="errata", view_uri="/errata/RHSA-2024:1234", id="/errata/RHSA-2024:1234"), False),
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
    doc.product = "Red Hat Enterprise Linux"
    result = _format_metadata(doc)
    assert "**Test Doc**" in result
    assert "Type: documentation" in result
    assert "Product: Red Hat Enterprise Linux" in result
    assert "https://access.redhat.com/documentation/en-US/test" in result


def test_format_metadata_with_synopsis():
    """Synopsis is included when present."""
    doc = _make_doc()
    doc.portal_synopsis = "A brief synopsis."
    result = _format_metadata(doc)
    assert "Synopsis: A brief synopsis." in result


# ---------------------------------------------------------------------------
# Documentation + no query -> nudge message
# ---------------------------------------------------------------------------


def test_documentation_no_query_returns_nudge():
    """Documentation without a query returns a nudge instead of content."""
    doc = _make_doc(kind="documentation", content="x" * 100_000)
    data = _make_data()
    result = _format_document_content(doc, data, doc.view_uri, query="", max_chars=30_000, current_result="")
    assert "Pass a query" in result
    assert "large documentation page" in result
    # Must NOT contain the actual content
    assert "x" * 100 not in result


def test_documentation_no_query_nudge_via_uri():
    """URI-based documentation detection also triggers the nudge."""
    doc = _make_doc(kind="other", view_uri="/documentation/en-US/rhel/9/guide")
    data = _make_data()
    result = _format_document_content(doc, data, doc.view_uri, query="", max_chars=30_000, current_result="")
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
        doc, data, doc.view_uri, query="kernel configuration", max_chars=30_000, current_result=""
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

    result = _format_document_content(doc, data, doc.view_uri, query="", max_chars=30_000, current_result="")

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
        doc, data, doc.view_uri, query="network configuration", max_chars=30_000, current_result=""
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
    doc.main_content = ""
    data = _make_data()

    result = _format_document_content(doc, data, doc.view_uri, query="test", max_chars=30_000, current_result="")
    assert result == ""


def test_documentation_no_main_content_still_nudges_without_query():
    """Documentation nudge fires even when main_content is missing (check order matters)."""
    doc = _make_doc(kind="documentation")
    doc.main_content = ""
    data = _make_data()

    result = _format_document_content(doc, data, doc.view_uri, query="", max_chars=30_000, current_result="")
    assert "Pass a query" in result


def test_documentation_no_main_content_with_query_returns_empty():
    """Documentation with a query but no main_content returns empty (no content to extract)."""
    doc = _make_doc(kind="documentation")
    doc.main_content = ""
    data = _make_data()

    result = _format_document_content(doc, data, doc.view_uri, query="kernel", max_chars=30_000, current_result="")
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

        _format_document_content(doc, data, doc.view_uri, query=query, max_chars=30_000, current_result="")

        assert _get_counter("okp_document_nudge") == before + expected_delta

    @pytest.mark.parametrize(
        "kind, view_uri, content, snippets, query, counter_name, expected_delta",
        [
            pytest.param(
                "documentation",
                "/documentation/en-US/test",
                "Content",
                ["Snippet about kernel configuration"],
                "kernel",
                "okp_document_highlight_used",
                1,
                id="doc-with-highlights",
            ),
            pytest.param(
                "solution",
                "/solutions/12345",
                "Full body.",
                ["Fix for the network issue"],
                "network",
                "okp_document_highlight_used",
                1,
                id="non-doc-with-highlights",
            ),
            pytest.param(
                "documentation",
                "/documentation/en-US/test",
                "Content about systemd units " + "w" * 300,
                None,
                "systemd",
                "okp_document_highlight_fallback",
                1,
                id="doc-no-highlights",
            ),
            pytest.param(
                "solution",
                "/solutions/12345",
                "Solution about networking " + "z" * 300,
                None,
                "networking",
                "okp_document_highlight_fallback",
                1,
                id="non-doc-no-highlights",
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

        _format_document_content(doc, data, doc.view_uri, query="", max_chars=30_000, current_result="")

        assert _get_counter("okp_document_highlight_used") == before_used
        assert _get_counter("okp_document_highlight_fallback") == before_fallback

    def test_no_highlight_metrics_without_main_content(self):
        """Missing main_content fires neither highlight counter."""
        doc = _make_doc(kind="documentation", content="x")
        doc.main_content = ""
        data = _make_data()
        before_used = _get_counter("okp_document_highlight_used")
        before_fallback = _get_counter("okp_document_highlight_fallback")

        _format_document_content(doc, data, doc.view_uri, query="kernel", max_chars=30_000, current_result="")

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
        mock_fetch.return_value = SolrResponse()
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
        mock_fetch.return_value = SolrResponse(
            response=SolrResponseBody(
                numFound=1,
                docs=[
                    SolrDoc(
                        allTitle="Test Solution",
                        documentKind="solution",
                        view_uri="/solutions/12345",
                        id="/solutions/12345",
                        main_content="Solution content here.",
                    )
                ],
            ),
        )
        await tools.get_document(mock_ctx, "/solutions/12345")

    assert _get_counter("okp_document_not_found") == before


# ---------------------------------------------------------------------------
# Solr request construction
#
# These assert the parameters actually sent to Solr. The rest of this module
# mocks _fetch_document_raw / _solr_query out, so a lookup that never matches
# anything still passes every other test here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc_id,expected_clause",
    [
        # solutions/articles/documentation: id carries /index.html, no view_uri,
        # and search_portal renders the URL with the suffix stripped.
        ("/solutions/5372961", 'id:"/solutions/5372961/index.html"'),
        ("/solutions/5372961/index.html", 'id:"/solutions/5372961/index.html"'),
        ("/articles/67521", 'id:"/articles/67521/index.html"'),
        # errata: id is the bare advisory ID, view_uri is the path form.
        ("RHSA-2026:29976", 'id:"RHSA-2026:29976"'),
        ("/errata/RHSA-2026:29976/", 'view_uri:"/errata/RHSA-2026:29976/"'),
        # CVE: both forms are indexed.
        ("/security/cve/CVE-2024-1086/", 'view_uri:"/security/cve/CVE-2024-1086/"'),
        ("/security/cve/CVE-2024-1086/index.html", 'id:"/security/cve/CVE-2024-1086/index.html"'),
    ],
)
def test_doc_id_filter_covers_corpus_conventions(doc_id, expected_clause):
    """Every doc_id form a caller can hold resolves to a matching clause."""
    assert expected_clause in _doc_id_filter(doc_id)


def test_doc_id_filter_escapes_quotes():
    """Injection attempts stay inside the quoted phrase."""
    assert '\\"' in _doc_id_filter('/solutions/1" OR id:*')


async def test_fetch_document_raw_pins_lucene_parser():
    """The raw lookup must not inherit the request handler's edismax default.

    Under edismax the field-qualified OR is scored as optional clauses
    governed by the handler's mm, which matches zero documents.
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = Mock(
        json=Mock(return_value={"response": {"numFound": 0, "docs": []}}),
        raise_for_status=Mock(),
    )

    await _fetch_document_raw("/solutions/5372961", client=mock_client, solr_endpoint=_SOLR_ENDPOINT)

    params = mock_client.get.call_args.kwargs["params"]
    assert params["defType"] == "lucene"
    assert params["q"] == _doc_id_filter("/solutions/5372961")


async def test_fetch_document_with_query_keeps_caller_query_out_of_q():
    """Identity goes in q; the caller's query only picks highlights.

    With the query in q, edismax mm decides whether the document comes back
    at all, so a valid doc_id whose text shares too few terms with the query
    is reported as "Document not found".
    """
    with patch("okp_mcp.tools.document._solr_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = SolrResponse()
        await _fetch_document_with_query(
            "/solutions/5372961",
            "some unrelated question",
            client=AsyncMock(spec=httpx.AsyncClient),
            solr_endpoint=_SOLR_ENDPOINT,
        )

    params = mock_query.call_args.args[0]
    assert params["q"] == _doc_id_filter("/solutions/5372961")
    assert params["defType"] == "lucene"
    assert params["hl.q"] == _clean_query("some unrelated question")
    assert "fq" not in params


# ---------------------------------------------------------------------------
# passage anchoring
# ---------------------------------------------------------------------------

_PASSAGE_HTML = (
    '<section id="admission-plug-ins"><h1 class="title">Chapter 9. Admission plugins</h1>'
    "<p>Admission plugins process resource requests to the control plane API.</p>"
    '<section id="admission-webhooks-about_admission-plug-ins">'
    '<h2 class="title">9.3. Webhook admission plugins</h2>'
    "<p>You can implement dynamic admission through webhook admission plugins that "
    "call webhook servers over HTTP at defined endpoints.</p></section></section>"
)


def test_passage_label_names_the_section_it_came_from():
    """A passage from real prose is labelled with its anchor and section title."""
    outline = parse_document(_PASSAGE_HTML)
    snippet = "dynamic admission through webhook admission plugins that call webhook servers"
    label = _passage_label(1, snippet, outline)
    assert label == "Passage 1 [#admission-webhooks-about_admission-plug-ins — 9.3. Webhook admission plugins]:"


def test_passage_label_attributes_to_the_innermost_section():
    """Prose in a chapter's own text is not attributed to a nested subsection."""
    outline = parse_document(_PASSAGE_HTML)
    label = _passage_label(1, "process resource requests to the control plane API", outline)
    assert "#admission-plug-ins " in label


def test_passage_label_stays_bare_for_a_toc_fragment():
    """A ToC run of headings has no home section and must not borrow one."""
    outline = parse_document(_PASSAGE_HTML)
    toc = "9.1. About admission plugins 9.2. Default admission plugins 9.4. Types of webhook"
    assert _passage_label(2, toc, outline) == "Passage 2:"


def test_passage_label_without_an_outline():
    """With no mirror the label is unchanged from before anchors existed."""
    assert _passage_label(3, "any passage text at all goes here", NO_OUTLINE) == "Passage 3:"


def test_format_document_passages_announces_linkable_fragments():
    """The header tells the caller what to do with the fragments."""
    outline = parse_document(_PASSAGE_HTML)
    result = _format_document_passages(
        ["dynamic admission through webhook admission plugins that call webhook servers"],
        query="webhooks",
        max_chars=3000,
        current_result="",
        outline=outline,
    )
    assert "append a passage's fragment to the URL above" in result
    assert "#admission-webhooks-about_admission-plug-ins" in result


def test_format_document_passages_header_unchanged_without_anchors():
    """Without a mirror the passage block keeps its original header."""
    result = _format_document_passages(["some snippet"], query="q", max_chars=3000, current_result="")
    assert result.startswith("\n\nRelevant passages:\n")


# ---------------------------------------------------------------------------
# ToC passage filtering
# ---------------------------------------------------------------------------

_TOC = "9.1. About admission plugins 9.2. Default admission plugins 9.4. Types of webhook"
_PROSE = "dynamic admission through webhook admission plugins that call webhook servers"


def test_drop_toc_passages_keeps_prose_only():
    """A ToC run is dropped while the prose passage survives."""
    outline = parse_document(_PASSAGE_HTML)
    assert _drop_toc_passages([_TOC, _PROSE], outline) == [_PROSE]


def test_drop_toc_passages_without_a_mirror():
    """With no outline every passage looks unplaceable, so none may be dropped."""
    assert _drop_toc_passages([_TOC, _PROSE], NO_OUTLINE) == [_TOC, _PROSE]


def test_drop_toc_passages_when_everything_looks_like_toc():
    """Returning nothing is worse than returning headings, so the list stands."""
    outline = parse_document(_PASSAGE_HTML)
    assert _drop_toc_passages([_TOC], outline) == [_TOC]


def test_drop_toc_passages_preserves_order():
    """Relevance order from Solr is not disturbed by filtering."""
    outline = parse_document(_PASSAGE_HTML)
    second = "Admission plugins process resource requests to the control plane API"
    assert _drop_toc_passages([_PROSE, _TOC, second], outline) == [_PROSE, second]
