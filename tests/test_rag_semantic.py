"""Tests for semantic (KNN vector) search against portal-rag."""

import httpx
import pytest
import respx

from okp_mcp.rag.semantic import semantic_search

SOLR_URL = "http://localhost:8983"
SEMANTIC_ENDPOINT = f"{SOLR_URL}/solr/portal-rag/semantic-search"
VALID_VECTOR = [0.01] * 384

RAG_SEMANTIC_RESPONSE = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "doc_id": "/security/cve/CVE-2024-42225_chunk_2",
                "title": "CVE-2024-42225 - Red Hat Customer Portal",
                "chunk": "A potential flaw was found...",
                "headings": "CVE-2024-42225,Description",
                "chunk_index": 2,
                "num_tokens": 49,
                "online_source_url": "https://access.redhat.com/security/cve/cve-2024-42225",
                "score": 0.95,
            }
        ],
    }
}


async def test_semantic_search_sends_get_to_semantic_search_endpoint(rag_client):
    """semantic_search sends GET to {solr_url}/solr/portal-rag/semantic-search."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result == RAG_SEMANTIC_RESPONSE


async def test_semantic_search_includes_knn_query_format(rag_client):
    """semantic_search request params include q with KNN format: {!knn f=chunk_vector topK=...}[...]."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL, max_results=10)

    assert route.called
    call_params = route.calls[0].request.url.params
    assert call_params["q"].startswith("{!knn f=chunk_vector topK=10}")
    assert call_params["q"].endswith("]")
    assert "0.01" in call_params["q"]


async def test_semantic_search_uses_max_results_as_topk(rag_client):
    """semantic_search uses max_results as topK value in KNN query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL, max_results=25)

    assert route.called
    call_params = route.calls[0].request.url.params
    assert "topK=25" in call_params["q"]
    assert call_params["rows"] == "25"


async def test_semantic_search_includes_fq_is_chunk_true(rag_client):
    """semantic_search request params include fq=is_chunk:true."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    call_params = route.calls[0].request.url.params
    assert call_params["fq"] == "is_chunk:true"


async def test_semantic_search_returns_raw_dict_from_rag_query(rag_client):
    """semantic_search returns raw dict from rag_query()."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert isinstance(result, dict)
    assert "response" in result
    assert result["response"]["numFound"] == 1


@pytest.mark.parametrize(
    ("dimension", "expected_in_error"),
    [
        (128, "128"),
        (256, "256"),
        (512, "512"),
    ],
    ids=["dim-128", "dim-256", "dim-512"],
)
async def test_semantic_search_raises_value_error_on_wrong_dimension(rag_client, dimension, expected_in_error):
    """semantic_search raises ValueError when vector length is not 384."""
    vector = [0.01] * dimension

    with pytest.raises(ValueError, match=expected_in_error):
        await semantic_search(vector, client=rag_client, solr_url=SOLR_URL)


async def test_semantic_search_dimension_error_mentions_expected_384(rag_client):
    """semantic_search ValueError message includes the expected dimension (384)."""
    vector = [0.01] * 256

    with pytest.raises(ValueError, match="384"):
        await semantic_search(vector, client=rag_client, solr_url=SOLR_URL)


async def test_semantic_search_succeeds_with_valid_384_dimension_vector(rag_client):
    """semantic_search succeeds with valid 384-dimensional vector."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result == RAG_SEMANTIC_RESPONSE
