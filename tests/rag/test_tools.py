"""Unit tests for search_rag MCP tool."""

from unittest.mock import MagicMock

import httpx
import respx
from fastmcp import Context

from okp_mcp.rag.tools import search_rag
from okp_mcp.server import AppContext

RAG_SOLR_URL = "http://test-solr:8984"
HYBRID_ENDPOINT = f"{RAG_SOLR_URL}/solr/portal-rag/hybrid-search"


def _make_app_context(client: httpx.AsyncClient, max_chars: int = 50_000) -> AppContext:
    """Create a minimal AppContext for testing."""
    return AppContext(
        http_client=client,
        solr_endpoint="http://test-solr:8983",
        max_response_chars=max_chars,
        rag_solr_url=RAG_SOLR_URL,
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
