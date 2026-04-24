"""Prometheus metrics, HTTP middleware, and /metrics scrape endpoint for the OKP MCP server."""

import time

from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE  # noqa: N812 -- upstream naming
from prometheus_client import Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from okp_mcp.server import mcp

# ---------------------------------------------------------------------------
# HTTP-level metrics (recorded by PrometheusMiddleware)
# ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "okp_http_requests_total",
    "Total HTTP requests received by the MCP server",
    ["method", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "okp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "status"],
)

# ---------------------------------------------------------------------------
# MCP tool metrics (recorded by tool instrumentation in tools/)
# ---------------------------------------------------------------------------
TOOL_CALLS = Counter(
    "okp_tool_calls_total",
    "Total MCP tool invocations",
    ["tool"],
)
TOOL_DURATION = Histogram(
    "okp_tool_duration_seconds",
    "MCP tool execution duration in seconds",
    ["tool"],
)

# ---------------------------------------------------------------------------
# Solr backend metrics (recorded by solr.py instrumentation)
# ---------------------------------------------------------------------------
SOLR_QUERIES = Counter(
    "okp_solr_queries_total",
    "Total Solr queries executed",
    ["status"],
)
SOLR_QUERY_DURATION = Histogram(
    "okp_solr_query_duration_seconds",
    "Solr query round-trip duration in seconds",
    ["status"],
)


class PrometheusMiddleware:
    """ASGI middleware that records HTTP request count and duration."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Track request metrics for HTTP connections, pass through everything else."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        start = time.monotonic()
        status_code = 500
        recorded = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, recorded
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Record TTFB so SSE/streaming connections don't inflate the histogram
                # with full connection lifetime.
                if not recorded:
                    duration = time.monotonic() - start
                    HTTP_REQUESTS.labels(method=method, status=str(status_code)).inc()
                    HTTP_REQUEST_DURATION.labels(method=method, status=str(status_code)).observe(duration)
                    recorded = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Fallback for cases where headers never sent (e.g. app crash before response).
            if not recorded:
                duration = time.monotonic() - start
                HTTP_REQUESTS.labels(method=method, status=str(status_code)).inc()
                HTTP_REQUEST_DURATION.labels(method=method, status=str(status_code)).observe(duration)


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request: Request) -> Response:
    """Expose Prometheus metrics for scraping."""
    del request
    return Response(content=generate_latest(), media_type=_CONTENT_TYPE)
