"""Tests for lexical search against the portal-rag Solr core."""

import httpx
import respx

from okp_mcp.rag.lexical import lexical_search

LEXICAL_ENDPOINT = "http://localhost:8983/solr/portal-rag/select"
SOLR_URL = "http://localhost:8983"


async def test_lexical_search_sends_get_to_select_endpoint(rag_client, rag_chunk_response):
    """lexical_search sends GET to {solr_url}/solr/portal-rag/select."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        result = await lexical_search("kernel panic", client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result == rag_chunk_response


async def test_lexical_search_includes_deftype_edismax(rag_client, rag_chunk_response):
    """lexical_search includes defType=edismax in request params."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["defType"] == "edismax"


async def test_lexical_search_includes_field_boosts(rag_client, rag_chunk_response):
    """lexical_search includes qf=title^20 chunk^10 in request params."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["qf"] == "title^20 chunk^10"


async def test_lexical_search_includes_rows_parameter(rag_client, rag_chunk_response):
    """lexical_search includes rows equal to max_results in request params."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("test query", client=rag_client, solr_url=SOLR_URL, max_results=25)

    call_params = route.calls[0].request.url.params
    assert call_params["rows"] == "25"


async def test_lexical_search_includes_chunk_filter(rag_client, rag_chunk_response):
    """lexical_search includes fq=is_chunk:true in request params."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["fq"] == "is_chunk:true"


async def test_lexical_search_passes_query_as_q_parameter(rag_client, rag_chunk_response):
    """lexical_search passes query string as q parameter."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("kernel panic error", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["q"] == "kernel panic error"


async def test_lexical_search_default_max_results_is_10(rag_client, rag_chunk_response):
    """lexical_search uses default max_results of 10 when not specified."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["rows"] == "10"


async def test_lexical_search_passes_empty_query_as_is(rag_client, rag_chunk_response):
    """lexical_search passes empty query string through as-is."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await lexical_search("", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert call_params["q"] == ""


async def test_lexical_search_returns_raw_dict_from_rag_query(rag_client, rag_chunk_response):
    """lexical_search returns raw dict from rag_query()."""
    with respx.mock(assert_all_called=True) as router:
        router.get(LEXICAL_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        result = await lexical_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert isinstance(result, dict)
    assert "response" in result
    assert result["response"]["numFound"] == 1
    assert len(result["response"]["docs"]) == 1
    assert result["response"]["docs"][0]["doc_id"] == "/security/cve/CVE-2024-42225_chunk_2"
