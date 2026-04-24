"""Tests for Prometheus metrics, middleware, and /metrics endpoint."""

from unittest.mock import MagicMock

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from okp_mcp.config import ServerConfig
from okp_mcp.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    SOLR_QUERIES,
    SOLR_QUERY_DURATION,
    TOOL_CALLS,
    TOOL_DURATION,
    PrometheusMiddleware,
    metrics_endpoint,
)
from okp_mcp.solr import _solr_query

_SOLR_ENDPOINT = ServerConfig().solr_endpoint


def _get_counter(name: str, labels: dict) -> float:
    """Read the current value of a Prometheus counter, defaulting to 0."""
    return REGISTRY.get_sample_value(f"{name}_total", labels) or 0.0


def _get_histogram_count(name: str, labels: dict | None = None) -> float:
    """Read the sample count of a Prometheus histogram."""
    return REGISTRY.get_sample_value(f"{name}_count", labels or {}) or 0.0


# ---------------------------------------------------------------------------
# PrometheusMiddleware
# ---------------------------------------------------------------------------


async def test_prometheus_middleware_records_http_request_counter():
    """Middleware increments the request counter with method and status labels."""
    sent: list[dict] = []

    async def mock_app(scope, receive, send):
        """Minimal ASGI app returning 200."""
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def mock_receive():
        """Return an empty HTTP request body."""
        return {"type": "http.request", "body": b""}

    async def mock_send(message):
        """Capture sent ASGI messages."""
        sent.append(message)

    before = _get_counter("okp_http_requests", {"method": "POST", "status": "200"})

    middleware = PrometheusMiddleware(mock_app)
    await middleware({"type": "http", "method": "POST", "path": "/mcp"}, mock_receive, mock_send)

    after = _get_counter("okp_http_requests", {"method": "POST", "status": "200"})
    assert after == before + 1
    assert len(sent) == 2


async def test_prometheus_middleware_records_duration_histogram():
    """Middleware observes request duration in the histogram."""
    sent: list[dict] = []

    async def mock_app(scope, receive, send):
        """Minimal ASGI app returning 200."""
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def mock_send(message):
        """Capture sent ASGI messages."""
        sent.append(message)

    async def mock_receive():
        """Return an empty HTTP request body."""
        return {"type": "http.request", "body": b""}

    before = _get_histogram_count("okp_http_request_duration_seconds", {"method": "GET", "status": "200"})

    middleware = PrometheusMiddleware(mock_app)
    await middleware({"type": "http", "method": "GET", "path": "/metrics"}, mock_receive, mock_send)

    after = _get_histogram_count("okp_http_request_duration_seconds", {"method": "GET", "status": "200"})
    assert after == before + 1


async def test_prometheus_middleware_captures_error_status_codes():
    """Middleware records non-200 status codes from the downstream app."""
    sent: list[dict] = []

    async def error_app(scope, receive, send):
        """ASGI app returning 500."""
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b"error"})

    async def mock_send(message):
        """Capture sent ASGI messages."""
        sent.append(message)

    async def mock_receive():
        """Return an empty HTTP request body."""
        return {"type": "http.request", "body": b""}

    before = _get_counter("okp_http_requests", {"method": "POST", "status": "500"})

    middleware = PrometheusMiddleware(error_app)
    await middleware({"type": "http", "method": "POST", "path": "/mcp"}, mock_receive, mock_send)

    after = _get_counter("okp_http_requests", {"method": "POST", "status": "500"})
    assert after == before + 1


async def test_prometheus_middleware_passes_through_non_http_scopes():
    """Non-HTTP scopes (websocket, lifespan) are forwarded without metrics."""
    called = False

    async def mock_app(scope, receive, send):
        """Track that the app was called."""
        nonlocal called
        called = True

    before_get = _get_counter("okp_http_requests", {"method": "GET", "status": "200"})
    before_post = _get_counter("okp_http_requests", {"method": "POST", "status": "200"})

    middleware = PrometheusMiddleware(mock_app)
    await middleware({"type": "websocket"}, None, None)

    assert called
    # No HTTP metrics should have been recorded.
    assert _get_counter("okp_http_requests", {"method": "GET", "status": "200"}) == before_get
    assert _get_counter("okp_http_requests", {"method": "POST", "status": "200"}) == before_post


async def test_prometheus_middleware_records_metrics_on_app_exception():
    """Metrics are recorded even when the downstream app raises."""

    async def crashing_app(scope, receive, send):
        """ASGI app that crashes before sending a response."""
        raise RuntimeError("boom")

    before = _get_counter("okp_http_requests", {"method": "POST", "status": "500"})
    before_duration = _get_histogram_count("okp_http_request_duration_seconds", {"method": "POST", "status": "500"})

    async def receive():
        """Stub ASGI receive callable."""
        return {"type": "http.request", "body": b""}

    async def send(msg):
        """Stub ASGI send callable."""

    middleware = PrometheusMiddleware(crashing_app)
    with pytest.raises(RuntimeError, match="boom"):
        await middleware(
            {"type": "http", "method": "POST", "path": "/mcp"},
            receive,
            send,
        )

    # Status defaults to 500 when no http.response.start was sent.
    after = _get_counter("okp_http_requests", {"method": "POST", "status": "500"})
    after_duration = _get_histogram_count("okp_http_request_duration_seconds", {"method": "POST", "status": "500"})
    assert after == before + 1
    assert after_duration == before_duration + 1


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


async def test_metrics_endpoint_returns_prometheus_format():
    """The /metrics custom route returns Prometheus exposition text format."""
    response = await metrics_endpoint(MagicMock())
    assert response.status_code == 200
    assert b"okp_http_requests_total" in response.body
    assert b"okp_tool_calls_total" in response.body
    assert b"okp_solr_queries_total" in response.body


async def test_metrics_endpoint_content_type():
    """The /metrics response uses the standard Prometheus content type."""
    response = await metrics_endpoint(MagicMock())
    assert "text/plain" in response.media_type


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------


def test_metric_label_names():
    """Verify expected label names on each metric family."""
    assert HTTP_REQUESTS._labelnames == ("method", "status")
    assert HTTP_REQUEST_DURATION._labelnames == ("method", "status")
    assert TOOL_CALLS._labelnames == ("tool",)
    assert TOOL_DURATION._labelnames == ("tool",)
    assert SOLR_QUERIES._labelnames == ("status",)
    assert SOLR_QUERY_DURATION._labelnames == ("status",)


# ---------------------------------------------------------------------------
# Solr query metrics integration
# ---------------------------------------------------------------------------


async def test_solr_query_records_success_metrics(sample_solr_response):
    """Successful Solr queries increment the success counter and record duration."""
    before_success = _get_counter("okp_solr_queries", {"status": "success"})
    before_duration = _get_histogram_count("okp_solr_query_duration_seconds", {"status": "success"})

    with respx.mock(assert_all_called=True) as router:
        router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        await _solr_query({"q": "test"}, solr_endpoint=_SOLR_ENDPOINT)

    assert _get_counter("okp_solr_queries", {"status": "success"}) == before_success + 1
    assert _get_histogram_count("okp_solr_query_duration_seconds", {"status": "success"}) == before_duration + 1


@pytest.mark.parametrize(
    ("status_label", "mock_kwargs", "expected_exc"),
    [
        pytest.param(
            "timeout",
            {"side_effect": httpx.TimeoutException("slow")},
            httpx.TimeoutException,
            id="timeout",
        ),
        pytest.param(
            "error",
            {"return_value": httpx.Response(500, text="Internal Server Error")},
            httpx.HTTPStatusError,
            id="http-500",
        ),
    ],
)
async def test_solr_query_records_failure_metrics(status_label, mock_kwargs, expected_exc):
    """Failed Solr queries increment the matching failure counter."""
    before = _get_counter("okp_solr_queries", {"status": status_label})

    with respx.mock(assert_all_called=True) as router:
        router.get(_SOLR_ENDPOINT).mock(**mock_kwargs)
        with pytest.raises(expected_exc):
            await _solr_query({"q": "test"}, solr_endpoint=_SOLR_ENDPOINT)

    assert _get_counter("okp_solr_queries", {"status": status_label}) == before + 1


async def test_solr_query_records_error_metrics_on_solr_error_response():
    """Solr responses containing an 'error' key increment the error counter."""
    before = _get_counter("okp_solr_queries", {"status": "error"})
    error_response = {"error": {"msg": "bad query", "code": 400}}

    with respx.mock(assert_all_called=True) as router:
        router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=error_response))
        result = await _solr_query({"q": "bad"}, solr_endpoint=_SOLR_ENDPOINT)

    assert _get_counter("okp_solr_queries", {"status": "error"}) == before + 1
    assert result["response"]["numFound"] == 0


async def test_solr_query_always_records_duration():
    """Duration histogram is recorded on both success and failure paths."""
    before = _get_histogram_count("okp_solr_query_duration_seconds", {"status": "timeout"})

    with respx.mock:
        respx.get(_SOLR_ENDPOINT).mock(side_effect=httpx.TimeoutException("slow"))
        with pytest.raises(httpx.TimeoutException):
            await _solr_query({"q": "test"}, solr_endpoint=_SOLR_ENDPOINT)

    assert _get_histogram_count("okp_solr_query_duration_seconds", {"status": "timeout"}) == before + 1
