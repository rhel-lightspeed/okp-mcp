"""Tests for SOLR query client lifecycle behavior."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx

from okp_mcp.config import SOLR_ENDPOINT
from okp_mcp.solr import _solr_query


async def test_solr_query_uses_provided_shared_client(sample_solr_response):
    """Provided client is used directly instead of constructing a new one."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        shared_client = httpx.AsyncClient(timeout=30.0)
        try:
            with patch(
                "okp_mcp.solr.httpx.AsyncClient", side_effect=AssertionError("constructor should not be called")
            ):
                data = await _solr_query({"q": "kernel panic"}, client=shared_client)
        finally:
            await shared_client.aclose()

    assert route.called
    assert data == sample_solr_response


async def test_solr_query_does_not_close_shared_client():
    """Provided shared client is not closed by _solr_query."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": {"numFound": 0, "docs": []}, "highlighting": {}}

    shared_client = AsyncMock(spec=httpx.AsyncClient)
    shared_client.get = AsyncMock(return_value=response)
    shared_client.aclose = AsyncMock()

    await _solr_query({"q": "rpm-ostree"}, client=shared_client)

    shared_client.get.assert_awaited_once()
    shared_client.aclose.assert_not_awaited()


async def test_solr_query_creates_and_closes_client_when_not_provided(sample_solr_response):
    """When no client is provided, _solr_query creates and closes one."""
    created_client = httpx.AsyncClient(timeout=30.0)
    original_aclose = created_client.aclose
    closed = False

    async def _tracking_aclose() -> None:
        nonlocal closed
        closed = True
        await original_aclose()

    created_client.aclose = _tracking_aclose

    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client) as client_ctor,
        respx.mock(assert_all_called=True) as router,
    ):
        route = router.get(SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        data = await _solr_query({"q": "openshift"})

    client_ctor.assert_called_once_with(timeout=30.0)
    assert route.called
    assert closed
    assert data == sample_solr_response


@pytest.mark.parametrize("use_shared_client", [True, False])
async def test_solr_query_timeout_exception_propagates(use_shared_client):
    """TimeoutException is re-raised for shared and owned client paths."""
    timeout_error = httpx.TimeoutException("slow SOLR")

    if use_shared_client:
        shared_client = AsyncMock(spec=httpx.AsyncClient)
        shared_client.get = AsyncMock(side_effect=timeout_error)
        with pytest.raises(httpx.TimeoutException):
            await _solr_query({"q": "timeout"}, client=shared_client)
        shared_client.aclose.assert_not_awaited()
        return

    created_client = AsyncMock(spec=httpx.AsyncClient)
    created_client.get = AsyncMock(side_effect=timeout_error)
    created_client.aclose = AsyncMock()
    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client),
        pytest.raises(httpx.TimeoutException),
    ):
        await _solr_query({"q": "timeout"})
    created_client.aclose.assert_awaited_once()


@pytest.mark.parametrize("use_shared_client", [True, False])
async def test_solr_query_http_status_error_propagates(use_shared_client):
    """HTTPStatusError is re-raised for shared and owned client paths."""
    request = httpx.Request("GET", SOLR_ENDPOINT)
    response = httpx.Response(503, request=request, text="service unavailable")
    status_error = httpx.HTTPStatusError("bad status", request=request, response=response)

    if use_shared_client:
        shared_client = AsyncMock(spec=httpx.AsyncClient)
        shared_client.get = AsyncMock(side_effect=status_error)
        with pytest.raises(httpx.HTTPStatusError):
            await _solr_query({"q": "status"}, client=shared_client)
        shared_client.aclose.assert_not_awaited()
        return

    created_client = AsyncMock(spec=httpx.AsyncClient)
    created_client.get = AsyncMock(side_effect=status_error)
    created_client.aclose = AsyncMock()
    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await _solr_query({"q": "status"})
    created_client.aclose.assert_awaited_once()


async def test_solr_query_respx_regression_guard(solr_mock, sample_solr_response):
    """Existing respx endpoint mocking keeps working after client refactor."""
    data = await _solr_query({"q": "selinux"})

    assert solr_mock.called
    assert data["response"]["numFound"] == sample_solr_response["response"]["numFound"]
