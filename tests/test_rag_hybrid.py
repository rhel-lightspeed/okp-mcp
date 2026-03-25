"""Tests for hybrid search against the portal-rag /hybrid-search handler."""

import httpx
import respx

from okp_mcp.rag.hybrid import hybrid_search

HYBRID_ENDPOINT = "http://localhost:8983/solr/portal-rag/hybrid-search"
SOLR_URL = "http://localhost:8983"


async def test_hybrid_search_sends_get_to_hybrid_search_endpoint(rag_client, rag_chunk_response):
    """hybrid_search sends GET to {solr_url}/solr/portal-rag/hybrid-search."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        result = await hybrid_search("firewalld RHEL 9", client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result == rag_chunk_response


async def test_hybrid_search_includes_query_in_params(rag_client, rag_chunk_response):
    """hybrid_search includes q parameter with the query string."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("firewalld RHEL 9", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["q"] == "firewalld RHEL 9"


async def test_hybrid_search_includes_rows_equal_to_max_results(rag_client, rag_chunk_response):
    """hybrid_search includes rows parameter equal to max_results."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL, max_results=25)

    call_params = route.calls[0].request.url.params
    assert call_params["rows"] == "25"


async def test_hybrid_search_includes_is_chunk_filter(rag_client, rag_chunk_response):
    """hybrid_search includes fq=is_chunk:true filter."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["fq"] == "is_chunk:true"


async def test_hybrid_search_does_not_send_edismax_defaults(rag_client, rag_chunk_response):
    """hybrid_search does not send defType or qf (server-side handler has config)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert "defType" not in call_params
    assert "qf" not in call_params


async def test_hybrid_search_default_max_results_is_10(rag_client, rag_chunk_response):
    """hybrid_search defaults to max_results=10 when not specified."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["rows"] == "10"


async def test_hybrid_search_returns_raw_dict_from_rag_query(rag_client):
    """hybrid_search returns the raw dict from rag_query()."""
    custom_response = {
        "response": {
            "numFound": 2,
            "docs": [
                {"doc_id": "doc1", "chunk": "content1"},
                {"doc_id": "doc2", "chunk": "content2"},
            ],
        }
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=custom_response))
        result = await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result == custom_response
    assert result["response"]["numFound"] == 2
    assert len(result["response"]["docs"]) == 2
