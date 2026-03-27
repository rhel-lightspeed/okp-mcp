"""Unit tests for search_rag MCP tool."""

import logging
from unittest.mock import MagicMock, patch

import httpx
import respx
from fastmcp import Context

from okp_mcp.rag.embeddings import Embedder
from okp_mcp.rag.models import RagDocument, RagResponse
from okp_mcp.rag.tools import search_rag
from okp_mcp.server import AppContext

RAG_SOLR_URL = "http://test-solr:8984"
HYBRID_ENDPOINT = f"{RAG_SOLR_URL}/solr/portal-rag/hybrid-search"


def _make_app_context(client: httpx.AsyncClient, max_chars: int = 50_000, embedder=None) -> AppContext:
    """Create a minimal AppContext for testing."""
    return AppContext(
        http_client=client,
        solr_endpoint="http://test-solr:8983",
        max_response_chars=max_chars,
        rag_solr_url=RAG_SOLR_URL,
        embedder=embedder,
    )


def _make_ctx(app: AppContext) -> Context:
    """Create a mock Context with the given AppContext in lifespan_context."""
    ctx = MagicMock(spec=Context)
    ctx.lifespan_context = {"app": app}
    return ctx


async def test_search_rag_returns_formatted_results(rag_client, rag_chunk_response):
    """search_rag returns markdown-formatted output with title and URL."""
    response_with_url = {
        "response": {
            "numFound": 1,
            "docs": [
                {
                    **rag_chunk_response["response"]["docs"][0],
                    "online_source_url": "https://access.redhat.com/security/cve/CVE-2024-42225",
                }
            ],
        }
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=response_with_url))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "CVE firewall")

    assert "CVE-2024-42225" in result
    assert "https://access.redhat.com" in result
    assert "Found" in result


async def test_search_rag_deduplicates_chunks_from_same_parent(rag_client):
    """search_rag deduplicates chunks from the same parent document."""
    two_chunk_response = {
        "response": {
            "numFound": 2,
            "docs": [
                {
                    "doc_id": "/cve_chunk_1",
                    "parent_id": "/cve-doc",
                    "title": "CVE Title",
                    "chunk": "chunk one content here",
                    "chunk_index": 1,
                    "num_tokens": 50,
                },
                {
                    "doc_id": "/cve_chunk_2",
                    "parent_id": "/cve-doc",
                    "title": "CVE Title",
                    "chunk": "chunk two content here",
                    "chunk_index": 2,
                    "num_tokens": 50,
                },
            ],
        }
    }
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=two_chunk_response))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "CVE test")

    assert "Found 1 results" in result


async def test_search_rag_rejects_empty_query(rag_client):
    """search_rag returns early for empty or whitespace-only queries."""
    app = _make_app_context(rag_client)
    ctx = _make_ctx(app)
    for q in ["", "   ", "\t"]:
        result = await search_rag(ctx, q)
        assert "provide a search query" in result.lower()


async def test_search_rag_clamps_max_results(rag_client, rag_chunk_response):
    """search_rag clamps max_results to [1, 20] range."""
    # max_results=0 should clamp to 1 (min)
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        await search_rag(ctx, "test", max_results=0)
        first_call_rows = int(router.calls[0].request.url.params["rows"])
        assert first_call_rows == 1 * 3  # 3x over-fetch, min clamped to 1

    # max_results=100 should clamp to 20 (max)
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        await search_rag(ctx, "test", max_results=100)
        call_rows = int(router.calls[0].request.url.params["rows"])
        assert call_rows == 20 * 3  # 3x over-fetch, max clamped to 20


async def test_search_rag_truncates_at_response_budget(rag_client):
    """search_rag truncates output at max_response_chars and adds a notice."""
    many_docs = {
        "response": {
            "numFound": 5,
            "docs": [
                {
                    "doc_id": f"/doc_{i}",
                    "parent_id": f"/parent_{i}",
                    "title": f"Doc {i}",
                    "chunk": "x" * 500,
                    "num_tokens": 100,
                }
                for i in range(5)
            ],
        }
    }
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=many_docs))
        app = _make_app_context(rag_client, max_chars=100)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "test", max_results=5)

    assert "response size limit reached" in result
    assert "Showing" in result


async def test_search_rag_returns_no_results_message(rag_client):
    """search_rag returns a user-friendly message when Solr returns no docs."""
    empty_response = {"response": {"numFound": 0, "docs": []}}
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=empty_response))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "nonexistent query xyz")

    assert result == "No results found for: nonexistent query xyz"


async def test_search_rag_handles_timeout(rag_client):
    """search_rag returns a user-friendly message on timeout."""
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(side_effect=httpx.TimeoutException("timed out"))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "test query")

    assert "timed out" in result.lower()
    assert "try again" in result.lower()


async def test_search_rag_handles_http_error(rag_client):
    """search_rag returns a user-friendly message on HTTP 500 error."""
    with respx.mock() as router:
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(500, text="Internal Server Error"))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "test query")

    assert "unavailable" in result.lower()


async def test_search_rag_registered_in_mcp_with_rag_tag():
    """search_rag is registered in FastMCP with the 'rag' tag and ctx excluded from schema."""
    from okp_mcp.server import mcp

    # Use _get_tool() which bypasses the enable/disable state set by the lifespan
    tool = await mcp._get_tool("search_rag")
    assert tool is not None, "search_rag tool not registered"
    assert "rag" in tool.tags, f"Expected 'rag' tag, got: {tool.tags}"
    # ctx should NOT appear in the input schema (FastMCP injects it automatically)
    schema_props = tool.parameters.get("properties", {})
    assert "ctx" not in schema_props, f"ctx should not be in input schema, got: {list(schema_props)}"


# --- Fused search path tests ---


async def test_search_rag_fused_path_with_embedder(rag_client, rag_chunk_response):
    """search_rag calls _run_fused_search when embedder is available."""
    embedder = MagicMock(spec=Embedder)
    fused_response = RagResponse(
        num_found=1,
        docs=[RagDocument(doc_id="/doc1", parent_id="/parent1", chunk="fused content", num_tokens=50)],
    )
    with patch("okp_mcp.rag.tools._run_fused_search", return_value=fused_response) as mock_fused:
        app = _make_app_context(rag_client, embedder=embedder)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "test query")

    mock_fused.assert_awaited_once()
    assert "fused content" in result
    assert "Found" in result


async def test_search_rag_fused_uses_raw_query_for_semantic(rag_client):
    """semantic_text_search receives raw query; hybrid_search receives cleaned query."""
    embedder = MagicMock(spec=Embedder)
    raw_query = "how to configure firewall on RHEL"
    hybrid_resp = RagResponse(
        num_found=1,
        docs=[RagDocument(doc_id="/h1", parent_id="/p1", chunk="hybrid result", num_tokens=50)],
    )
    semantic_resp = RagResponse(
        num_found=1,
        docs=[RagDocument(doc_id="/s1", parent_id="/ps1", chunk="semantic result", num_tokens=50)],
    )
    rrf_resp = RagResponse(num_found=2, docs=hybrid_resp.docs + semantic_resp.docs)

    with (
        patch("okp_mcp.rag.tools.hybrid_search", return_value=hybrid_resp) as mock_hybrid,
        patch("okp_mcp.rag.tools.semantic_text_search", return_value=semantic_resp) as mock_semantic,
        patch("okp_mcp.rag.tools.reciprocal_rank_fusion", return_value=rrf_resp),
    ):
        app = _make_app_context(rag_client, embedder=embedder)
        ctx = _make_ctx(app)
        await search_rag(ctx, raw_query)

    # Semantic gets raw query; hybrid gets cleaned (stopwords stripped)
    semantic_call_text = mock_semantic.call_args[0][0]
    hybrid_call_query = mock_hybrid.call_args[0][0]
    assert semantic_call_text == raw_query
    hybrid_words = hybrid_call_query.split()
    assert "how" not in hybrid_words  # stopwords removed
    assert "to" not in hybrid_words
    assert "on" not in hybrid_words


async def test_search_rag_falls_back_to_hybrid_on_semantic_failure(rag_client, caplog):
    """search_rag falls back to hybrid results when semantic search raises."""
    embedder = MagicMock(spec=Embedder)
    hybrid_resp = RagResponse(
        num_found=1,
        docs=[RagDocument(doc_id="/h1", parent_id="/p1", chunk="hybrid only content", num_tokens=50)],
    )

    with (
        patch("okp_mcp.rag.tools.hybrid_search", return_value=hybrid_resp),
        patch("okp_mcp.rag.tools.semantic_text_search", side_effect=httpx.ConnectError("semantic down")),
        caplog.at_level(logging.WARNING),
    ):
        app = _make_app_context(rag_client, embedder=embedder)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "firewall configuration")

    # Should return hybrid results, not raise
    assert "Found" in result
    assert "hybrid only content" in result
    # Warning should be logged
    assert any("Semantic search failed" in r.message for r in caplog.records)


async def test_search_rag_hybrid_only_when_no_embedder(rag_client, rag_chunk_response):
    """search_rag uses hybrid-only path when AppContext.embedder is None."""
    with (
        respx.mock() as router,
        patch("okp_mcp.rag.tools.semantic_text_search") as mock_semantic,
    ):
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        app = _make_app_context(rag_client)  # embedder=None by default
        ctx = _make_ctx(app)
        await search_rag(ctx, "firewall")

    mock_semantic.assert_not_called()


async def test_search_rag_calls_expand_chunks_after_dedup(rag_client, rag_chunk_response):
    """expand_chunks is called with deduped docs on all paths (hybrid-only)."""
    expand_calls = []

    async def _tracking_expand(chunks, **kwargs):
        expand_calls.append(chunks)
        return chunks

    with (
        respx.mock() as router,
        patch("okp_mcp.rag.tools.expand_chunks", side_effect=_tracking_expand),
    ):
        router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        app = _make_app_context(rag_client)
        ctx = _make_ctx(app)
        await search_rag(ctx, "CVE test")

    assert len(expand_calls) == 1
    assert isinstance(expand_calls[0], list)


async def test_search_rag_handles_hybrid_failure_in_fused_path(rag_client):
    """search_rag returns user-friendly message when hybrid fails in fused path."""
    embedder = MagicMock(spec=Embedder)

    with (
        patch("okp_mcp.rag.tools.hybrid_search", side_effect=httpx.ConnectError("solr down")),
        patch("okp_mcp.rag.tools.semantic_text_search", side_effect=httpx.ConnectError("semantic down")),
    ):
        app = _make_app_context(rag_client, embedder=embedder)
        ctx = _make_ctx(app)
        result = await search_rag(ctx, "test query")

    assert "unavailable" in result.lower()
