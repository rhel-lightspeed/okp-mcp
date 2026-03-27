"""Tests for FastMCP instance configuration and tool registration."""

# pyright: reportMissingImports=false

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

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
    ],
)
def test_mcp_properties(attr, expected):
    """FastMCP instance has the expected name and instructions."""
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
        rag_solr_url="http://localhost:8983",
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


@pytest.mark.asyncio
async def test_lifespan_rag_solr_url_falls_back_to_solr_url():
    """When MCP_RAG_SOLR_URL not set, lifespan falls back to solr_url."""
    async with _app_lifespan(mcp) as lifespan_context:
        app_context = lifespan_context["app"]
        assert app_context.rag_solr_url == "http://localhost:8983"


@pytest.mark.asyncio
async def test_lifespan_uses_explicit_rag_solr_url():
    """When MCP_RAG_SOLR_URL is set, lifespan uses it directly."""
    with patch.dict("os.environ", {"MCP_RAG_SOLR_URL": "http://rag-instance:8984"}):
        from okp_mcp import server as server_module

        original = server_module._server_config
        try:
            server_module._server_config = None  # force fresh config load
            async with _app_lifespan(mcp) as lifespan_context:
                app_context = lifespan_context["app"]
                assert app_context.rag_solr_url == "http://rag-instance:8984"
        finally:
            server_module._server_config = original


@pytest.mark.asyncio
async def test_lifespan_creates_embedder_when_rag_enabled():
    """Lifespan creates an Embedder instance when MCP_RAG_SOLR_URL is set."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    mock_embedder = MagicMock()
    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with patch("okp_mcp.server.Embedder", return_value=mock_embedder):
            async with _app_lifespan(mcp) as lifespan_context:
                app = lifespan_context["app"]
                assert app.embedder is mock_embedder
    finally:
        server_module._server_config = original


@pytest.mark.asyncio
async def test_lifespan_embedder_is_none_when_rag_disabled():
    """Lifespan sets embedder to None when RAG is disabled."""
    async with _app_lifespan(mcp) as lifespan_context:
        app = lifespan_context["app"]
        assert app.embedder is None


@pytest.mark.asyncio
async def test_lifespan_embedder_graceful_degradation_on_init_failure():
    """Lifespan sets embedder to None if Embedder init raises."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with patch("okp_mcp.server.Embedder", side_effect=OSError("model not found")):
            async with _app_lifespan(mcp) as lifespan_context:
                app = lifespan_context["app"]
                assert app.embedder is None
    finally:
        server_module._server_config = original


@pytest.mark.asyncio
async def test_lifespan_closes_embedder_on_normal_exit():
    """Lifespan calls embedder.close() when context exits normally."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    mock_embedder = MagicMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with (
            patch("okp_mcp.server.Embedder", return_value=mock_embedder),
            patch("okp_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            async with _app_lifespan(mcp):
                pass
    finally:
        server_module._server_config = original

    mock_embedder.close.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_closes_embedder_on_exception():
    """Lifespan calls embedder.close() even if an error happens inside."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    mock_embedder = MagicMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with (
            patch("okp_mcp.server.Embedder", return_value=mock_embedder),
            patch("okp_mcp.server.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RuntimeError, match="boom"),
        ):
            async with _app_lifespan(mcp):
                raise RuntimeError("boom")
    finally:
        server_module._server_config = original

    mock_embedder.close.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_logs_warning_on_embedder_init_failure(caplog):
    """Lifespan logs a warning when Embedder initialization fails."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with patch("okp_mcp.server.Embedder", side_effect=OSError("model not found")):
            async with _app_lifespan(mcp):
                pass
    finally:
        server_module._server_config = original

    assert "Embedding model unavailable" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_logs_warning_with_traceback_on_embedder_close_failure(caplog):
    """Lifespan logs a warning with exc_info when embedder.close() fails."""
    from okp_mcp import server as server_module
    from okp_mcp.config import ServerConfig

    mock_embedder = MagicMock()
    mock_embedder.close.side_effect = OSError("device busy")
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    original = server_module._server_config
    try:
        server_module._server_config = ServerConfig(rag_solr_url="http://rag-test:8984")
        with (
            patch("okp_mcp.server.Embedder", return_value=mock_embedder),
            patch("okp_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            async with _app_lifespan(mcp):
                pass
    finally:
        server_module._server_config = original

    assert "Failed to close embedder cleanly" in caplog.text
    assert "device busy" in caplog.text
