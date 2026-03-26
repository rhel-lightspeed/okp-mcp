"""Tests for RAG query runner and constants."""

import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from okp_mcp.rag.common import rag_query

RAG_ENDPOINT = "http://localhost:8983/solr/portal-rag/select"


async def test_rag_query_sends_get_with_merged_params(rag_client):
    """rag_query sends GET to endpoint with params merged with wt=json."""
    response_data = {"response": {"numFound": 1, "docs": [{"id": "doc1"}]}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(RAG_ENDPOINT).mock(return_value=httpx.Response(200, json=response_data))
        result = await rag_query(RAG_ENDPOINT, {"q": "test", "rows": 10}, rag_client)

    assert route.called
    assert result.num_found == 1
    assert len(result.docs) == 1
    assert result.docs[0].id == "doc1"
    call_params = route.calls[0].request.url.params
    assert call_params["wt"] == "json"
    assert call_params["q"] == "test"
    assert call_params["rows"] == "10"


@pytest.mark.parametrize(
    "mock_response",
    [
        httpx.Response(200, json={"error": {"code": 400, "msg": "Invalid query"}}),
        httpx.Response(200, json={"responseHeader": {"status": 0}}),
        httpx.Response(200, json={"response": {"numFound": 0, "docs": "not a list"}}),
        httpx.Response(200, json={"response": {"docs": [{"id": "x"}]}}),
        httpx.Response(200, text="not json"),
    ],
    ids=["solr-error", "missing-response-key", "invalid-docs", "missing-numFound", "non-json-body"],
)
async def test_rag_query_returns_empty_response_on_bad_data(rag_client, mock_response):
    """rag_query returns an empty RagResponse for malformed or non-JSON Solr responses."""
    with respx.mock(assert_all_called=True) as router:
        router.get(RAG_ENDPOINT).mock(return_value=mock_response)
        result = await rag_query(RAG_ENDPOINT, {"q": "test"}, rag_client)

    assert result.num_found == 0
    assert result.docs == []


@pytest.mark.parametrize(
    "exception",
    [
        httpx.TimeoutException("slow query"),
        httpx.ConnectError("connection refused"),
    ],
    ids=["timeout", "connect-error"],
)
async def test_rag_query_reraises_http_exceptions(exception):
    """rag_query re-raises httpx transport exceptions from client.get()."""
    shared_client = AsyncMock(spec=httpx.AsyncClient)
    shared_client.get = AsyncMock(side_effect=exception)

    with pytest.raises(type(exception)):
        await rag_query(RAG_ENDPOINT, {"q": "test"}, shared_client)


async def test_rag_query_logs_at_info_level(rag_client, caplog):
    """rag_query logs at INFO level for successful queries."""
    response_data = {"response": {"numFound": 2, "docs": [{"id": "doc1"}, {"id": "doc2"}]}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(RAG_ENDPOINT).mock(return_value=httpx.Response(200, json=response_data))
        with caplog.at_level(logging.INFO):
            result = await rag_query(RAG_ENDPOINT, {"q": "test"}, rag_client)

    assert route.called
    assert result.num_found == 2
    assert any("RAG query" in record.message for record in caplog.records if record.levelno == logging.INFO)
