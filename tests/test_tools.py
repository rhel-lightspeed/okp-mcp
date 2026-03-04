"""Tests for the solr_query demo tool."""

import httpx
import pytest
import respx

from okp_mcp.config import ServerConfig
from okp_mcp.tools import solr_query


@pytest.fixture
def solr_endpoint():
    """Return the Solr endpoint URL for mocking."""
    return ServerConfig().solr_endpoint


@pytest.mark.asyncio
async def test_solr_query_returns_results(solr_endpoint, sample_solr_response):
    """Successful query formats doc titles, types, and URLs."""
    with respx.mock:
        respx.get(solr_endpoint).mock(return_value=httpx.Response(200, json=sample_solr_response))
        result = await solr_query("test query")

    assert "Found 1 result(s)" in result
    assert "Test Document" in result
    assert "documentation" in result
    assert "https://access.redhat.com" in result


@pytest.mark.asyncio
async def test_solr_query_empty_results(solr_endpoint, empty_solr_response):
    """Zero-result query returns a friendly message."""
    with respx.mock:
        respx.get(solr_endpoint).mock(return_value=httpx.Response(200, json=empty_solr_response))
        result = await solr_query("nonexistent thing")

    assert "No results found" in result


@pytest.mark.parametrize("query", ["", "   "])
@pytest.mark.asyncio
async def test_solr_query_rejects_blank(query):
    """Empty or whitespace-only queries return an error message without hitting Solr."""
    result = await solr_query(query)

    assert "Please provide a search query" in result


@pytest.mark.asyncio
async def test_solr_query_timeout(solr_endpoint):
    """Solr timeout returns a user-friendly error."""
    with respx.mock:
        respx.get(solr_endpoint).mock(side_effect=httpx.TimeoutException("timed out"))
        result = await solr_query("slow query")

    assert "timed out" in result.lower()


@pytest.mark.asyncio
async def test_solr_query_http_error(solr_endpoint):
    """Solr HTTP error returns a user-friendly error."""
    with respx.mock:
        respx.get(solr_endpoint).mock(return_value=httpx.Response(500))
        result = await solr_query("broken query")

    assert "unavailable" in result.lower()


@pytest.mark.asyncio
async def test_solr_query_clamps_max_results(solr_endpoint, sample_solr_response):
    """max_results is clamped to [1, 20]."""
    with respx.mock:
        route = respx.get(solr_endpoint).mock(return_value=httpx.Response(200, json=sample_solr_response))
        await solr_query("test", max_results=100)

    rows_param = route.calls[0].request.url.params.get("rows")
    assert rows_param == "20"


@pytest.mark.asyncio
async def test_solr_query_includes_product(solr_endpoint):
    """Product field is included in output when present in Solr doc."""
    response = {
        "responseHeader": {"status": 0},
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "allTitle": "RHEL 9 Guide",
                    "view_uri": "/docs/rhel9",
                    "documentKind": "documentation",
                    "product": "Red Hat Enterprise Linux",
                    "score": 5.0,
                }
            ],
        },
    }
    with respx.mock:
        respx.get(solr_endpoint).mock(return_value=httpx.Response(200, json=response))
        result = await solr_query("rhel 9")

    assert "Red Hat Enterprise Linux" in result
