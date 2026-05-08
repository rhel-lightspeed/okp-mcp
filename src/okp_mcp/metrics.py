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

# ---------------------------------------------------------------------------
# Search quality metrics (recorded by portal.py pipeline instrumentation)
# ---------------------------------------------------------------------------

# Distribution of final result counts returned to the caller.  The zero
# bucket doubles as a zero-result detector when read as a rate, but the
# dedicated counter below is cheaper for alerting.
SEARCH_RESULT_COUNT = Histogram(
    "okp_search_result_count",
    "Number of chunks returned by a portal search",
    buckets=(0, 1, 2, 3, 5, 7, 10, 15, 25),
)

# Fires when a portal search pipeline produces zero results, enabling
# direct alerting without histogram bucket arithmetic.
SEARCH_ZERO_RESULTS = Counter(
    "okp_search_zero_results_total",
    "Portal searches that returned zero results",
)

# Counts low-quality chunks removed by the score filter.  A sustained
# high rate may indicate Solr relevance tuning issues.
SEARCH_SCORE_FILTER_DROPPED = Counter(
    "okp_search_score_filter_dropped_total",
    "Chunks dropped by the score quality filter",
)

# Fires when at least one deprecation-sourced document survives into the
# final result set.  Useful for tracking how often users hit deprecated
# or removed features.
SEARCH_DEPRECATION_DETECTED = Counter(
    "okp_search_deprecation_detected_total",
    "Searches where deprecated/removed content appeared in results",
)


# ---------------------------------------------------------------------------
# Document retrieval metrics (recorded by tools/document.py instrumentation)
# ---------------------------------------------------------------------------

# Fires when a get_document lookup returns no matching Solr document,
# indicating the LLM passed a stale or fabricated doc ID.  A sustained
# high rate suggests search_portal result URLs are drifting from the
# Solr index.
DOCUMENT_NOT_FOUND = Counter(
    "okp_document_not_found_total",
    "Document ID lookups that returned no results",
)

# Fires when a documentation page is requested without a query, causing
# a nudge response instead of content.  Tracks how often LLMs skip the
# query parameter on large docs.
DOCUMENT_NUDGE = Counter(
    "okp_document_nudge_total",
    "Documentation pages served a nudge instead of content (no query)",
)

# Fires when Solr highlighting produces usable snippets for a document
# retrieval.  Compare against DOCUMENT_HIGHLIGHT_FALLBACK to gauge
# highlighting health.
DOCUMENT_HIGHLIGHT_USED = Counter(
    "okp_document_highlight_used_total",
    "Document retrievals that used Solr highlight snippets",
)

# Fires when a query-based document retrieval falls back to local BM25
# paragraph extraction because Solr returned no highlights.  A high
# ratio vs DOCUMENT_HIGHLIGHT_USED signals Solr highlighting issues.
DOCUMENT_HIGHLIGHT_FALLBACK = Counter(
    "okp_document_highlight_fallback_total",
    "Document retrievals that fell back to BM25 extraction (no highlights)",
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
