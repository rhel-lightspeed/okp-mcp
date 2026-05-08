"""Prometheus metrics, HTTP middleware, and /metrics scrape endpoint for the OKP MCP server."""

import time

from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE  # noqa: N812 -- upstream naming
from prometheus_client import Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from okp_mcp.server import mcp

# Allowlist of known paths to prevent unbounded label cardinality.
# Unknown paths are bucketed as "OTHER" so rogue or scan traffic
# cannot create arbitrary time series.
_KNOWN_PATHS: frozenset[str] = frozenset({"/mcp", "/sse", "/metrics"})

# ---------------------------------------------------------------------------
# HTTP-level metrics (recorded by PrometheusMiddleware)
# ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "okp_http_requests_total",
    "Total HTTP requests received by the MCP server",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "okp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path", "status"],
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
# Intent detection metrics (recorded by intent.py instrumentation)
# ---------------------------------------------------------------------------

# Tracks which intent rules fire and on which query path (main vs deprecation).
# Labels: intent=<rule name>, query_path=<"main"|"deprecation">
# Bounded cardinality: current 12 rule names x 2 paths = 24 series.
INTENT_MATCHED = Counter(
    "okp_intent_matched_total",
    "Intent rule matched a user query",
    ["intent", "query_path"],
)

# Incremented when no intent rule matches a query, indicating a coverage gap
# in the intent registry.  High values suggest new rules are needed.
# Labels: query_path=<"main"|"deprecation">
INTENT_NO_MATCH = Counter(
    "okp_intent_no_match_total",
    "No intent rule matched the user query",
    ["query_path"],
)

# Counts queries where an intent rule matched in the deprecation path but
# the rule has no dep_title_terms, causing an early return with no boost.
# Distinguishes "deliberately no deprecation boost" from "no match at all".
# Labels: intent=<rule name that caused the skip>
INTENT_DEPRECATION_SKIPPED = Counter(
    "okp_intent_deprecation_skipped_total",
    "Intent matched but skipped deprecation boost (no dep_title_terms)",
    ["intent"],
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
        raw_path = scope.get("path", "UNKNOWN")
        path = raw_path if raw_path in _KNOWN_PATHS else "OTHER"
        start = time.monotonic()
        status_code = 500
        recorded = False

        def _emit_metrics(current_status_code: int) -> None:
            """Record HTTP request count and duration once per request."""
            duration = time.monotonic() - start
            labels = {"method": method, "path": path, "status": str(current_status_code)}
            HTTP_REQUESTS.labels(**labels).inc()
            HTTP_REQUEST_DURATION.labels(**labels).observe(duration)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, recorded
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Record TTFB so SSE/streaming connections don't inflate the histogram
                # with full connection lifetime.
                if not recorded:
                    _emit_metrics(status_code)
                    recorded = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Fallback for cases where headers never sent (e.g. app crash before response).
            if not recorded:
                _emit_metrics(status_code)


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request: Request) -> Response:
    """Expose Prometheus metrics for scraping."""
    del request
    return Response(content=generate_latest(), media_type=_CONTENT_TYPE)
