"""Functional tests for context expansion against a live portal-rag Solr instance.

Requires the redhat-okp-rag container running on localhost:8984.
Deselected by default; run with: uv run pytest -m functional -k test_context
"""

import httpx
import pytest

from okp_mcp.rag.common import RAG_FL, rag_query
from okp_mcp.rag.context import expand_chunk, expand_chunks, fetch_parent_metadata, fetch_sibling_chunks
from okp_mcp.rag.models import RagDocument

pytestmark = pytest.mark.functional

RAG_SOLR_URL = "http://localhost:8984"
HYBRID_ENDPOINT = f"{RAG_SOLR_URL}/solr/portal-rag/hybrid-search"


@pytest.fixture(scope="module")
def _require_rag_solr():
    """Skip all tests in this module if the RAG Solr container is unavailable."""
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{RAG_SOLR_URL}/solr/portal-rag/admin/ping")
            if resp.status_code != 200:
                pytest.skip(f"portal-rag core not responding at {RAG_SOLR_URL}")
    except httpx.RequestError:
        pytest.skip(f"Solr not reachable at {RAG_SOLR_URL} - run: podman-compose up -d")


@pytest.fixture
async def client():
    """Async httpx client for functional tests."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        yield c


async def _search_chunk(client: httpx.AsyncClient, query: str) -> RagDocument:
    """Search for a single chunk via hybrid search, returning the top hit."""
    resp = await rag_query(
        HYBRID_ENDPOINT,
        {"q": query, "rows": 1, "fq": "is_chunk:true", "fl": RAG_FL},
        client,
    )
    assert resp.docs, f"No results for query: {query}"
    doc = resp.docs[0]
    assert doc.parent_id is not None
    assert doc.chunk_index is not None
    return doc


@pytest.mark.usefixtures("_require_rag_solr")
class TestContextExpansionFunctional:
    """Verify context expansion against a live portal-rag Solr core."""

    async def test_full_document_expansion_on_small_cve(self, client):
        """A small CVE document (< 4000 tokens) is fully retrieved and merged."""
        chunk = await _search_chunk(client, "CVE-2024-42225 MediaTek WiFi kernel")

        assert chunk.parent_id is not None
        parent = await fetch_parent_metadata(chunk.parent_id, client=client, solr_url=RAG_SOLR_URL)
        assert parent is not None
        assert parent.total_tokens is not None
        assert parent.total_tokens < 4000, f"Expected small doc, got {parent.total_tokens} tokens"

        expanded = await expand_chunk(chunk, client=client, solr_url=RAG_SOLR_URL)

        assert expanded.num_tokens == parent.total_tokens
        assert expanded.title == chunk.title
        assert expanded.online_source_url == chunk.online_source_url
        assert expanded.chunk is not None
        assert len(expanded.chunk) > len(chunk.chunk or "")

    async def test_windowed_expansion_on_large_doc(self, client):
        """A large documentation page (> 4000 tokens) gets windowed expansion."""
        chunk = await _search_chunk(client, "configuring SELinux booleans virtualization")

        assert chunk.parent_id is not None
        parent = await fetch_parent_metadata(chunk.parent_id, client=client, solr_url=RAG_SOLR_URL)
        assert parent is not None
        assert parent.total_tokens is not None
        assert parent.total_tokens > 4000, f"Expected large doc, got {parent.total_tokens} tokens"

        expanded = await expand_chunk(chunk, client=client, solr_url=RAG_SOLR_URL)

        assert expanded.num_tokens is not None
        assert expanded.num_tokens > (chunk.num_tokens or 0)
        assert expanded.num_tokens < parent.total_tokens
        assert expanded.title == chunk.title
        assert expanded.online_source_url == chunk.online_source_url

    async def test_sibling_chunks_ordered_by_index(self, client):
        """fetch_sibling_chunks returns chunks in ascending chunk_index order."""
        chunk = await _search_chunk(client, "firewalld RHEL 9 zones")

        assert chunk.parent_id is not None
        assert chunk.chunk_index is not None
        siblings = await fetch_sibling_chunks(
            chunk.parent_id,
            chunk.chunk_index,
            window=2,
            client=client,
            solr_url=RAG_SOLR_URL,
        )

        assert len(siblings) >= 2
        indices = [s.chunk_index for s in siblings if s.chunk_index is not None]
        assert indices == sorted(indices), f"Chunks not sorted: {indices}"
        assert all(s.parent_id == chunk.parent_id for s in siblings)

    async def test_expand_chunks_parallel(self, client):
        """expand_chunks handles multiple chunks from different parents concurrently."""
        resp = await rag_query(
            HYBRID_ENDPOINT,
            {"q": "RHEL security hardening", "rows": 5, "fq": "is_chunk:true", "fl": RAG_FL},
            client,
        )
        seen_parents: set[str] = set()
        diverse_chunks: list[RagDocument] = []
        for doc in resp.docs:
            if doc.parent_id and doc.parent_id not in seen_parents:
                seen_parents.add(doc.parent_id)
                diverse_chunks.append(doc)
            if len(diverse_chunks) >= 3:
                break

        expanded = await expand_chunks(diverse_chunks, client=client, solr_url=RAG_SOLR_URL)

        assert len(expanded) == len(diverse_chunks)
        for orig, exp in zip(diverse_chunks, expanded, strict=True):
            assert (exp.num_tokens or 0) >= (orig.num_tokens or 0)
            assert exp.title == orig.title
