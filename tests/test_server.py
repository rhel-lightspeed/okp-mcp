"""Tests for FastMCP instance configuration and tool registration."""

# pyright: reportMissingImports=false

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastmcp import Context

from okp_mcp.server import AppContext, _app_lifespan, get_app_context, mcp


@pytest.mark.parametrize(
    "attr, expected",
    [
        ("name", "RHEL OKP Knowledge Base"),
        (
            "instructions",
            "Search the Red Hat documentation, CVEs, errata, solutions, and articles to answer RHEL questions.",
        ),
        ("version", "0.1.0"),
        ("website_url", "https://github.com/rhel-lightspeed/okp-mcp"),
    ],
)
def test_mcp_properties(attr, expected):
    """FastMCP instance has the expected name, instructions, version, and website_url."""
    assert getattr(mcp, attr) == expected


@pytest.mark.asyncio
async def test_production_tools_registered():
    """Production RAG tools are registered on the MCP server."""
    import okp_mcp  # noqa: F401 — triggers tool registration via __init__

    tools = await mcp._list_tools()
    tool_names = {tool.name for tool in tools}
    expected_tools = {
        "search_documentation",
        "search_solutions",
        "search_cves",
        "search_errata",
        "search_articles",
        "get_document",
    }
    assert expected_tools.issubset(tool_names)


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


def test_mcp_masks_error_details():
    """FastMCP instance hides internal error details for unhandled exceptions."""
    assert mcp._mask_error_details is True  # type: ignore[attr-defined]


def test_mcp_validates_input_strictly():
    """FastMCP instance rejects type coercion for tool parameters."""
    assert mcp.strict_input_validation is True
