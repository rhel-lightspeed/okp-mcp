"""Functional tests for the search_rag pipeline against a live portal-rag Solr instance.

Exercises the full hybrid search pipeline (clean, search, deduplicate, format)
that the ``search_rag`` MCP tool orchestrates. Each test hits the real
``/hybrid-search`` Solr handler, so results depend on indexed content.

Requires the redhat-okp-rag container running on localhost:8984.
Deselected by default; run with: ``uv run pytest -m functional -k test_search``
"""

import httpx
import pytest

from okp_mcp.rag.common import RAG_FL, clean_rag_query
from okp_mcp.rag.formatting import deduplicate_chunks, format_rag_result
from okp_mcp.rag.hybrid import hybrid_search

pytestmark = pytest.mark.functional

RAG_SOLR_URL = "http://localhost:8984"


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


@pytest.mark.usefixtures("_require_rag_solr")
class TestSearchRagPipeline:
    """Verify the search_rag hybrid pipeline end-to-end against live portal-rag Solr."""

    async def test_hybrid_search_returns_chunks_with_required_fields(self, client: httpx.AsyncClient) -> None:
        """Hybrid search returns chunks with parent_id, chunk_index, title, and content."""
        query = clean_rag_query("How to configure firewalld in RHEL 9")
        response = await hybrid_search(query, client=client, solr_url=RAG_SOLR_URL, max_results=5, fl=RAG_FL)

        assert response.docs, "Expected results for a common RHEL topic"
        for doc in response.docs:
            assert doc.parent_id is not None, "Chunk missing parent_id"
            assert doc.chunk_index is not None, "Chunk missing chunk_index"
            assert doc.chunk, "Chunk has no content"
            assert doc.title, "Chunk missing title"

    async def test_deduplication_collapses_parent_duplicates(self, client: httpx.AsyncClient) -> None:
        """Over-fetching 3x produces duplicate parents that dedup collapses."""
        query = clean_rag_query("SELinux security policy RHEL")
        response = await hybrid_search(query, client=client, solr_url=RAG_SOLR_URL, max_results=30, fl=RAG_FL)

        assert len(response.docs) > 5, "Expected many chunks from over-fetch"

        parent_ids_before = [doc.parent_id for doc in response.docs if doc.parent_id]
        has_duplicates = len(parent_ids_before) > len(set(parent_ids_before))
        assert has_duplicates, "Expected duplicate parent_ids in a 30-result fetch"

        deduped = deduplicate_chunks(response.docs)
        parent_ids_after = [doc.parent_id for doc in deduped if doc.parent_id]
        assert len(parent_ids_after) == len(set(parent_ids_after)), "Dedup left duplicate parent_ids"
        assert len(deduped) < len(response.docs), "Dedup should reduce result count"

    async def test_product_boost_favors_matching_product(self, client: httpx.AsyncClient) -> None:
        """Product boost places matching-product docs in top results."""
        query = clean_rag_query("configuring virtual machines")

        response = await hybrid_search(
            query, client=client, solr_url=RAG_SOLR_URL, max_results=10, fl=RAG_FL, product="RHEL"
        )

        assert response.docs, "Boosted search returned no results"

        # CVE/errata chunks lack the product field, so only check docs that have it.
        top_with_product = [doc for doc in response.docs[:5] if doc.product]
        assert top_with_product, "No top results have a product field to verify boost"

        rhel_in_top = any(
            any("red hat enterprise linux" in p.lower() for p in doc.product or []) for doc in top_with_product
        )
        assert rhel_in_top, (
            f"RHEL product boost did not place any RHEL docs in top 5. "
            f"Top products: {[doc.product for doc in response.docs[:5]]}"
        )

    async def test_full_pipeline_produces_formatted_markdown(self, client: httpx.AsyncClient) -> None:
        """Complete pipeline (clean, search, dedup, format) produces structured markdown."""
        raw_query = "How do I configure hugepages on RHEL 9?"
        cleaned = clean_rag_query(raw_query)

        response = await hybrid_search(cleaned, client=client, solr_url=RAG_SOLR_URL, max_results=15, fl=RAG_FL)
        assert response.docs, "Pipeline got no search results"

        deduped = deduplicate_chunks(response.docs)[:5]
        assert deduped, "Pipeline got no results after dedup"

        for doc in deduped:
            formatted = format_rag_result(doc)
            assert formatted.startswith("**"), "Formatted result missing bold title"
            assert "URL:" in formatted, "Formatted result missing URL line"
            assert len(formatted) > 100, f"Formatted result suspiciously short ({len(formatted)} chars)"

    async def test_known_cve_is_findable(self, client: httpx.AsyncClient) -> None:
        """Searching for a known CVE ID returns a chunk referencing that CVE."""
        cve_id = "CVE-2024-42225"
        query = clean_rag_query(f"{cve_id} MediaTek WiFi kernel")

        response = await hybrid_search(query, client=client, solr_url=RAG_SOLR_URL, max_results=5, fl=RAG_FL)

        assert response.docs, f"No results for known {cve_id}"
        found = any(cve_id in (doc.title or "") or cve_id in (doc.chunk or "") for doc in response.docs)
        assert found, f"{cve_id} not found in any result title or chunk content"

    async def test_short_chunk_filtering_during_dedup(self, client: httpx.AsyncClient) -> None:
        """Dedup filters out very short chunks (< 30 tokens) when longer alternatives exist."""
        query = clean_rag_query("RHEL security hardening")
        response = await hybrid_search(query, client=client, solr_url=RAG_SOLR_URL, max_results=30, fl=RAG_FL)
        assert response.docs, "Expected results for dedup filtering test"

        deduped = deduplicate_chunks(response.docs, min_tokens=30)
        assert deduped, "Dedup unexpectedly returned no results"

        short_kept = [doc for doc in deduped if doc.num_tokens is not None and doc.num_tokens < 30]
        if not short_kept:
            pytest.skip("No short fallback chunks in current index snapshot")

        for doc in short_kept:
            # Verify this short chunk was kept only because no longer sibling exists
            siblings = [d for d in response.docs if d.parent_id == doc.parent_id]
            long_siblings = [s for s in siblings if s.num_tokens is None or s.num_tokens >= 30]
            assert not long_siblings, (
                f"Short chunk (num_tokens={doc.num_tokens}) kept despite longer siblings for parent {doc.parent_id}"
            )
