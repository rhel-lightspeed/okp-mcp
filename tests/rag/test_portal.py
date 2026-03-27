"""Tests for portal core search (solutions and articles)."""

import httpx
import pytest
import respx

from okp_mcp.rag.models import PortalResponse
from okp_mcp.rag.portal import PORTAL_FL, portal_search

PORTAL_ENDPOINT = "http://localhost:8984/solr/portal/select"
SOLR_URL = "http://localhost:8984"


@pytest.fixture
def portal_solution_response():
    """Realistic portal core solution response for mock Solr."""
    return {
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "id": "/solutions/3257611/index.html",
                    "documentKind": "solution",
                    "url_slug": "3257611",
                    "title": "usage of the service.alpha.kubernetes.io/tolerate-unready-endpoints annotation",
                    "main_content": "Solution Unverified - Updated 14 Jun 2024 Environment OpenShift...",
                    "heading_h2": ["environment", "issue", "resolution"],
                    "lastModifiedDate": "2024-06-14T17:18:29Z",
                }
            ],
        }
    }


@pytest.fixture
def portal_multi_response():
    """Portal response with mixed solution and article results."""
    return {
        "response": {
            "numFound": 2,
            "docs": [
                {
                    "id": "/solutions/12345/index.html",
                    "documentKind": "solution",
                    "title": "How to configure SELinux",
                    "main_content": "Environment RHEL 9...",
                },
                {
                    "id": "/articles/2585/index.html",
                    "documentKind": "article",
                    "title": "How do I debug startup scripts?",
                    "main_content": "How do I debug problems...",
                },
            ],
        }
    }


async def test_portal_search_sends_get_to_portal_select(rag_client, portal_solution_response):
    """portal_search sends GET to {solr_url}/solr/portal/select."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        result = await portal_search("SELinux RHEL 9", client=rag_client, solr_url=SOLR_URL)

    assert route.called
    assert result.num_found == 1


@pytest.mark.parametrize(
    ("param", "expected"),
    [
        ("defType", "edismax"),
        ("rows", "10"),
        ("wt", "json"),
        ("q", "test query"),
        ("fq", "{!terms f=documentKind}solution,article"),
    ],
    ids=["deftype-edismax", "default-rows-10", "wt-json", "query-passthrough", "default-fq-filter"],
)
async def test_portal_search_default_params(rag_client, portal_solution_response, param, expected):
    """portal_search sends correct default params to Solr."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert route.calls[0].request.url.params[param] == expected


async def test_portal_search_includes_field_boosts(rag_client, portal_solution_response):
    """portal_search includes qf with portal core field boosts."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    qf = route.calls[0].request.url.params["qf"]
    assert "url_slug^20" in qf
    assert "title^15" in qf
    assert "main_content^10" in qf


async def test_portal_search_custom_document_kinds(rag_client, portal_solution_response):
    """portal_search applies custom document_kinds as fq filter."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL, document_kinds=["solution"])

    assert route.calls[0].request.url.params["fq"] == "{!terms f=documentKind}solution"


async def test_portal_search_custom_max_results(rag_client, portal_solution_response):
    """portal_search passes max_results as rows parameter."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL, max_results=25)

    assert route.calls[0].request.url.params["rows"] == "25"


async def test_portal_search_sends_fl_when_provided(rag_client, portal_solution_response):
    """portal_search sends fl param to Solr when explicitly provided."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL, fl=PORTAL_FL)

    assert route.calls[0].request.url.params["fl"] == PORTAL_FL


async def test_portal_search_omits_fl_when_none(rag_client, portal_solution_response):
    """portal_search does not send fl param when fl is None (default)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert "fl" not in route.calls[0].request.url.params


async def test_portal_search_returns_portal_response(rag_client, portal_solution_response):
    """portal_search returns PortalResponse with correct types."""
    with respx.mock(assert_all_called=True) as router:
        router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_solution_response))
        result = await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert isinstance(result, PortalResponse)
    assert result.num_found == 1
    assert len(result.docs) == 1
    assert result.docs[0].id == "/solutions/3257611/index.html"
    assert result.docs[0].documentKind == "solution"


async def test_portal_search_returns_mixed_results(rag_client, portal_multi_response):
    """portal_search handles mixed solution and article results."""
    with respx.mock(assert_all_called=True) as router:
        router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=portal_multi_response))
        result = await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert result.num_found == 2
    assert result.docs[0].documentKind == "solution"
    assert result.docs[1].documentKind == "article"


@pytest.mark.parametrize(
    "exception_class",
    [httpx.TimeoutException, httpx.ConnectError],
    ids=["timeout", "connect-error"],
)
async def test_portal_search_propagates_http_exceptions(rag_client, exception_class):
    """portal_search propagates httpx transport exceptions."""
    with respx.mock(assert_all_called=True) as router:
        router.get(PORTAL_ENDPOINT).mock(side_effect=exception_class("error"))
        with pytest.raises(exception_class):
            await portal_search("test query", client=rag_client, solr_url=SOLR_URL)


@pytest.mark.parametrize(
    "bad_response",
    [
        {"error": {"msg": "undefined field", "code": 400}},
        {"unexpected": "structure"},
    ],
    ids=["solr-error", "unexpected-structure"],
)
async def test_portal_search_returns_empty_on_bad_data(rag_client, bad_response):
    """portal_search returns empty PortalResponse on malformed Solr responses."""
    with respx.mock(assert_all_called=True) as router:
        router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=bad_response))
        result = await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert result.num_found == 0
    assert result.docs == []


async def test_portal_search_returns_empty_on_invalid_document(rag_client):
    """portal_search returns empty PortalResponse when a doc fails Pydantic validation."""
    bad_doc_response = {
        "response": {
            "numFound": 1,
            "docs": [{"heading_h1": "not a list"}],
        }
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(PORTAL_ENDPOINT).mock(return_value=httpx.Response(200, json=bad_doc_response))
        result = await portal_search("test query", client=rag_client, solr_url=SOLR_URL)

    assert result.num_found == 0
    assert result.docs == []


async def test_portal_search_rejects_empty_document_kinds(rag_client):
    """portal_search raises ValueError when document_kinds is an empty list."""
    with pytest.raises(ValueError, match="document_kinds must not be empty"):
        await portal_search("test query", client=rag_client, solr_url=SOLR_URL, document_kinds=[])
