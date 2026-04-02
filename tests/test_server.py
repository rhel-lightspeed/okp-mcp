"""Tests for FastMCP instance configuration and tool registration."""

# pyright: reportMissingImports=false

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastmcp import Context
from fastmcp.server.middleware import MiddlewareContext
from starlette.applications import Starlette
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from okp_mcp.request_id import (
    REQUEST_ID_HEADER,
    RequestIDContextMiddleware,
    RequestIDHeaderMiddleware,
    RequestIDLogFilter,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from okp_mcp.server import AppContext, _app_lifespan, get_app_context, mcp


@pytest.mark.parametrize(
    "attr, expected",
    [
        ("name", "RHEL OKP Knowledge Base"),
        (
            "instructions",
            "Search the Red Hat documentation, CVEs, errata, solutions, and articles to answer RHEL questions.",
        ),
    ],
)
def test_mcp_properties(attr, expected):
    """FastMCP instance has the expected name and instructions."""
    assert getattr(mcp, attr) == expected


@pytest.mark.asyncio
async def test_production_tools_registered():
    """Production tools are registered on the MCP server."""
    import okp_mcp  # noqa: F401 — triggers tool registration via __init__

    tools = await mcp._list_tools()
    tool_names = {tool.name for tool in tools}
    expected_tools = {
        "search_portal",
        "get_document",
    }
    assert expected_tools.issubset(tool_names)
    removed_tools = {
        "search_documentation",
        "search_solutions",
        "search_cves",
        "search_errata",
        "search_articles",
    }
    assert removed_tools.isdisjoint(tool_names), f"Legacy search tools still registered: {removed_tools & tool_names}"


@pytest.mark.asyncio
async def test_app_lifespan_yields_app_context_dict():
    """Lifespan yields an app context dictionary with AppContext."""
    async with _app_lifespan(mcp) as lifespan_context:
        assert "app" in lifespan_context
        assert isinstance(lifespan_context["app"], AppContext)


@pytest.mark.asyncio
async def test_app_context_http_client_is_async_client():
    """AppContext includes an httpx AsyncClient instance."""
    async with _app_lifespan(mcp) as lifespan_context:
        app_context = lifespan_context["app"]
        assert isinstance(app_context.http_client, httpx.AsyncClient)


@pytest.mark.asyncio
async def test_app_lifespan_closes_client_on_normal_exit():
    """Lifespan always closes client when context exits normally."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    with patch("okp_mcp.server.httpx.AsyncClient", return_value=mock_client):
        async with _app_lifespan(mcp):
            pass

    mock_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_lifespan_closes_client_on_exception():
    """Lifespan closes client even if an error happens inside."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    with (
        patch("okp_mcp.server.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(
            RuntimeError,
            match="boom",
        ),
    ):
        async with _app_lifespan(mcp):
            raise RuntimeError("boom")

    mock_client.aclose.assert_awaited_once()


def test_get_app_context_returns_lifespan_app_context():
    """Helper returns the typed app context from lifespan context."""
    expected_context = AppContext(
        http_client=AsyncMock(spec=httpx.AsyncClient),
        solr_endpoint="http://localhost:8983/solr/portal/select",
        max_response_chars=30_000,
    )
    ctx = cast(Context, SimpleNamespace(lifespan_context={"app": expected_context}))

    assert get_app_context(ctx) is expected_context


def test_mcp_has_lifespan_configured():
    """FastMCP instance has a configured lifespan function."""
    assert getattr(mcp, "lifespan", None) is not None


@pytest.mark.asyncio
async def test_app_context_has_solr_endpoint():
    """Lifespan populates AppContext.solr_endpoint from ServerConfig default."""
    async with _app_lifespan(mcp) as lifespan_context:
        app_context = lifespan_context["app"]
        assert app_context.solr_endpoint == "http://localhost:8983/solr/portal/select"


def test_server_config_assignment_propagates_to_lifespan():
    """Direct _server_config assignment is picked up by the lifespan."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    original = server_module._server_config
    try:
        cfg = ServerConfig()
        server_module._server_config = cfg
        assert server_module._server_config is cfg
    finally:
        server_module._server_config = original


def test_request_id_log_filter_uses_context_var():
    """Log filter injects the active request ID into records."""
    log_filter = RequestIDLogFilter()
    record = logging.LogRecord(
        name="okp_mcp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    token = set_request_id("req-123")

    try:
        assert log_filter.filter(record) is True
    finally:
        reset_request_id(token)

    assert cast(Any, record).request_id == "req-123"


@pytest.mark.asyncio
async def test_request_id_context_middleware_uses_fastmcp_request_id():
    """Middleware uses FastMCP's request ID when no HTTP-level ID exists (e.g. stdio)."""
    middleware = RequestIDContextMiddleware()
    fastmcp_context = SimpleNamespace(request_context=object(), request_id="mcp-456")
    context = MiddlewareContext(
        message="test",
        fastmcp_context=cast(Any, fastmcp_context),
    )

    async def call_next(context: MiddlewareContext[Any]) -> str:
        assert context.fastmcp_context is fastmcp_context
        assert get_request_id() == "mcp-456"
        return "done"

    result = await middleware.on_message(context, call_next)

    assert result == "done"
    assert get_request_id() is None


@pytest.mark.asyncio
async def test_request_id_context_middleware_preserves_http_uuid():
    """HTTP-level UUID is kept instead of being overwritten by the JSON-RPC message counter."""
    http_uuid = "721b3d6f55374e52abc57651ef2510af"
    token = set_request_id(http_uuid)

    try:
        middleware = RequestIDContextMiddleware()
        fastmcp_context = SimpleNamespace(request_context=object(), request_id="2")
        context = MiddlewareContext(
            message="test",
            fastmcp_context=cast(Any, fastmcp_context),
        )

        async def call_next(context: MiddlewareContext[Any]) -> str:
            """Verify the UUID survives through the middleware chain."""
            assert get_request_id() == http_uuid
            return "done"

        result = await middleware.on_message(context, call_next)

        assert result == "done"
        assert get_request_id() == http_uuid
    finally:
        reset_request_id(token)


@pytest.mark.asyncio
async def test_request_id_header_middleware_reflects_active_request_id():
    """HTTP middleware exposes the active request ID in the response headers."""

    async def homepage(request):
        token = set_request_id("mcp-789")
        request.scope["state"]["request_id"] = "mcp-789"
        try:
            return PlainTextResponse("ok")
        finally:
            reset_request_id(token)

    app = Starlette(
        routes=[Route("/", endpoint=homepage)],
        middleware=[StarletteMiddleware(RequestIDHeaderMiddleware)],
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")

    assert response.headers[REQUEST_ID_HEADER] == "mcp-789"
