"""Tests for RAG query runner and constants."""

import logging
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from okp_mcp.rag.common import EMPTY_RAG_RESPONSE, rag_query

RAG_ENDPOINT = "http://localhost:8983/solr/portal-rag/select"


async def test_rag_query_sends_get_with_merged_params(rag_client):
    """rag_query sends GET to endpoint with params merged with wt=json."""
    response_data = {"response": {"numFound": 1, "docs": [{"id": "doc1"}]}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(RAG_ENDPOINT).mock(return_value=httpx.Response(200, json=response_data))
        result = await rag_query(RAG_ENDPOINT, {"q": "test", "rows": 10}, rag_client)

    assert route.called
    assert result == response_data
    call_params = route.calls[0].request.url.params
    assert call_params["wt"] == "json"
    assert call_params["q"] == "test"
    assert call_params["rows"] == "10"


@pytest.mark.parametrize(
    ("response_json", "_description"),
    [
        ({"error": {"code": 400, "msg": "Invalid query"}}, "Solr error key"),
        ({"responseHeader": {"status": 0}}, "missing response key"),
        ({"response": {"numFound": 0, "docs": "not a list"}}, "invalid docs structure"),
    ],
    ids=["solr-error", "missing-response-key", "invalid-docs"],
)
async def test_rag_query_returns_empty_response_on_malformed_data(rag_client, response_json, _description):
    """rag_query returns EMPTY_RAG_RESPONSE for various malformed Solr responses."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(RAG_ENDPOINT).mock(return_value=httpx.Response(200, json=response_json))
        result = await rag_query(RAG_ENDPOINT, {"q": "test"}, rag_client)

    assert route.called
    assert result == EMPTY_RAG_RESPONSE


@pytest.mark.parametrize(
    ("exception",),
    [
        (httpx.TimeoutException("slow query"),),
        (httpx.ConnectError("connection refused"),),
    ],
    ids=["timeout", "connect-error"],
)
async def test_rag_query_reraises_http_exceptions(exception):
    """rag_query re-raises httpx transport exceptions from client.get()."""
    shared_client = AsyncMock(spec=httpx.AsyncClient)
    shared_client.get = AsyncMock(side_effect=exception)

    with pytest.raises(type(exception)):
        await rag_query(RAG_ENDPOINT, {"q": "test"}, shared_client)


async def test_rag_query_returns_empty_response_on_non_json():
    """rag_query returns EMPTY_RAG_RESPONSE when response.json() raises ValueError."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("Invalid JSON")

    shared_client = AsyncMock(spec=httpx.AsyncClient)
    shared_client.get = AsyncMock(return_value=response)

    result = await rag_query(RAG_ENDPOINT, {"q": "test"}, shared_client)

    assert result == EMPTY_RAG_RESPONSE


async def test_rag_query_logs_at_info_level(rag_client, caplog):
    """rag_query logs at INFO level for successful queries."""
    response_data = {"response": {"numFound": 2, "docs": [{"id": "doc1"}, {"id": "doc2"}]}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(RAG_ENDPOINT).mock(return_value=httpx.Response(200, json=response_data))
        with caplog.at_level(logging.INFO):
            result = await rag_query(RAG_ENDPOINT, {"q": "test"}, rag_client)

    assert route.called
    assert result == response_data
    assert any("RAG query" in record.message for record in caplog.records if record.levelno == logging.INFO)
