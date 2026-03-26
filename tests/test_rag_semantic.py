"""Tests for semantic (KNN vector) search against portal-rag."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from okp_mcp.rag.models import RagResponse
from okp_mcp.rag.semantic import semantic_search, semantic_text_search

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
    assert result.num_found == 1


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


async def test_semantic_search_returns_rag_response(rag_client):
    """semantic_search returns RagResponse from rag_query()."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert isinstance(result, RagResponse)
    assert result.num_found == 1


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
    """semantic_search raises ValueError mentioning both actual and expected (384) dimensions."""
    vector = [0.01] * dimension

    with pytest.raises(ValueError, match=expected_in_error) as exc_info:
        await semantic_search(vector, client=rag_client, solr_url=SOLR_URL)
    assert "384" in str(exc_info.value)


async def test_semantic_search_succeeds_with_valid_384_dimension_vector(rag_client):
    """semantic_search succeeds with valid 384-dimensional vector."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_search(VALID_VECTOR, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result.num_found == 1


# --- semantic_text_search tests ---


@pytest.fixture()
def mock_embedder():
    """Provide an AsyncMock embedder that returns VALID_VECTOR from encode_async."""
    embedder = AsyncMock()
    embedder.encode_async = AsyncMock(return_value=VALID_VECTOR)
    return embedder


async def test_semantic_text_search_calls_encode_async_with_text(rag_client, mock_embedder):
    """semantic_text_search calls embedder.encode_async with the provided text."""
    with respx.mock:
        respx.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_text_search("some text", embedder=mock_embedder, client=rag_client, solr_url=SOLR_URL)

    mock_embedder.encode_async.assert_called_once_with("some text")


async def test_semantic_text_search_passes_vector_from_embedder_to_semantic_search(rag_client, mock_embedder):
    """semantic_text_search passes the embedder vector through to the Solr endpoint."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_text_search("query text", embedder=mock_embedder, client=rag_client, solr_url=SOLR_URL)

    assert route.called
    call_params = route.calls[0].request.url.params
    assert "0.01" in call_params["q"]


async def test_semantic_text_search_returns_solr_response(rag_client, mock_embedder):
    """semantic_text_search returns the raw Solr response dict."""
    with respx.mock:
        respx.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        result = await semantic_text_search("test query", embedder=mock_embedder, client=rag_client, solr_url=SOLR_URL)

    assert result.num_found == 1


async def test_semantic_text_search_propagates_max_results(rag_client, mock_embedder):
    """semantic_text_search passes max_results through to the Solr query rows param."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SEMANTIC_ENDPOINT).mock(return_value=httpx.Response(200, json=RAG_SEMANTIC_RESPONSE))
        await semantic_text_search(
            "test query", embedder=mock_embedder, client=rag_client, solr_url=SOLR_URL, max_results=25
        )

    assert route.called
    call_params = route.calls[0].request.url.params
    assert call_params["rows"] == "25"
    assert "topK=25" in call_params["q"]
