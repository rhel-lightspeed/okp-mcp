"""Tests for hybrid search against the portal-rag /hybrid-search handler."""

import httpx
import pytest
import respx

from okp_mcp.rag.hybrid import hybrid_search
from okp_mcp.rag.models import RagResponse

HYBRID_ENDPOINT = "http://localhost:8983/solr/portal-rag/hybrid-search"
SOLR_URL = "http://localhost:8983"


async def test_hybrid_search_sends_get_to_hybrid_search_endpoint(rag_client, rag_chunk_response):
    """hybrid_search sends GET to {solr_url}/solr/portal-rag/hybrid-search."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        result = await hybrid_search("firewalld RHEL 9", client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result.num_found == 1


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


async def test_hybrid_search_returns_rag_response(rag_client):
    """hybrid_search returns RagResponse from rag_query()."""
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
    assert isinstance(result, RagResponse)
    assert result.num_found == 2
    assert len(result.docs) == 2


async def test_hybrid_search_sends_fl_when_provided(rag_client, rag_chunk_response):
    """hybrid_search sends fl param to Solr when explicitly provided."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL, fl="doc_id,title,chunk")

    call_params = route.calls[0].request.url.params
    assert call_params["fl"] == "doc_id,title,chunk"


async def test_hybrid_search_omits_fl_when_none(rag_client, rag_chunk_response):
    """hybrid_search does not send fl param when fl is None (default)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test query", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert "fl" not in call_params


async def test_hybrid_search_product_boost_sends_bq(rag_client, rag_chunk_response):
    """hybrid_search with product adds bq boost param to Solr request."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("selinux", client=rag_client, solr_url=SOLR_URL, product="Red Hat Enterprise Linux")

    call_params = route.calls[0].request.url.params
    assert "bq" in call_params
    assert 'product:("Red Hat Enterprise Linux")' in call_params["bq"]


async def test_hybrid_search_product_none_no_bq(rag_client, rag_chunk_response):
    """hybrid_search with product=None does not send bq param."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("selinux", client=rag_client, solr_url=SOLR_URL)

    call_params = route.calls[0].request.url.params
    assert "bq" not in call_params


async def test_hybrid_search_empty_product_defaults_to_rhel(rag_client, rag_chunk_response):
    """hybrid_search with empty product defaults bq to RHEL boost."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("selinux", client=rag_client, solr_url=SOLR_URL, product="")

    call_params = route.calls[0].request.url.params
    assert "bq" in call_params
    assert "Red Hat Enterprise Linux" in call_params["bq"]


@pytest.mark.parametrize(
    ("product_input", "expected_bq_product"),
    [
        ("RHEL", "Red Hat Enterprise Linux"),
        ("OCP", "Red Hat OpenShift Container Platform"),
        ("Fedora", "Fedora"),
    ],
    ids=["alias-rhel", "alias-ocp", "unknown-passthrough"],
)
async def test_hybrid_search_product_alias_normalization(
    rag_client, rag_chunk_response, product_input, expected_bq_product
):
    """hybrid_search normalizes known aliases and passes unknown products through in bq."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test", client=rag_client, solr_url=SOLR_URL, product=product_input)

    call_params = route.calls[0].request.url.params
    assert f'product:("{expected_bq_product}")' in call_params["bq"]


async def test_hybrid_search_product_strips_quotes_to_prevent_injection(rag_client, rag_chunk_response):
    """hybrid_search strips double quotes from product to prevent Solr query injection."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test", client=rag_client, solr_url=SOLR_URL, product='mal")^999 OR (*:*')

    call_params = route.calls[0].request.url.params
    bq = call_params["bq"]
    assert bq == 'product:("mal)^999 OR (*:*")^10'
    assert '")^999' not in bq


async def test_hybrid_search_product_strips_backslashes(rag_client, rag_chunk_response):
    """hybrid_search strips backslashes from product to prevent escaping the closing quote."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test", client=rag_client, solr_url=SOLR_URL, product="test\\")

    call_params = route.calls[0].request.url.params
    bq = call_params["bq"]
    assert bq == 'product:("test")^10'
    assert "\\" not in bq


async def test_hybrid_search_product_uses_bq_not_fq(rag_client, rag_chunk_response):
    """hybrid_search uses bq (boost) not fq (filter) for product, so CVEs/errata aren't dropped."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(HYBRID_ENDPOINT).mock(return_value=httpx.Response(200, json=rag_chunk_response))
        await hybrid_search("test", client=rag_client, solr_url=SOLR_URL, product="RHEL")

    call_params = route.calls[0].request.url.params
    # fq should only contain is_chunk:true, NOT any product filter
    assert call_params["fq"] == "is_chunk:true"
    assert "bq" in call_params
