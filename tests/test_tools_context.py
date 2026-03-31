"""Tests for tool context parameter and shared HTTP client wiring."""

# pyright: reportMissingImports=false

import inspect
from unittest.mock import AsyncMock, Mock, patch

import httpx

import okp_mcp  # noqa: F401 -- triggers @mcp.tool registration
from okp_mcp import tools
from okp_mcp.config import ServerConfig
from okp_mcp.server import mcp
from okp_mcp.tools import _format_document

_SOLR_ENDPOINT = ServerConfig().solr_endpoint


def test_all_mcp_tools_accept_ctx_parameter():
    """All MCP tool functions expose a context parameter for lifespan access."""
    tool_names = [
        "search_portal",
        "get_document",
        "run_code",
    ]

    for tool_name in tool_names:
        signature = inspect.signature(getattr(tools, tool_name))
        assert "ctx" in signature.parameters


async def test_fetch_document_raw_uses_provided_client_without_constructing_or_closing_it():
    """Provided client path in _fetch_document_raw bypasses client construction and closing."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": {"numFound": 1, "docs": [{"id": "123"}]}}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=response)
    mock_client.aclose = AsyncMock()

    with patch("okp_mcp.tools.httpx.AsyncClient", side_effect=AssertionError("constructor should not be called")):
        data = await tools._fetch_document_raw("/solutions/123", client=mock_client, solr_endpoint=_SOLR_ENDPOINT)

    mock_client.get.assert_awaited_once()
    mock_client.aclose.assert_not_awaited()
    assert data["response"]["numFound"] == 1


async def test_fetch_document_raw_creates_and_closes_client_when_not_provided():
    """Owned client path in _fetch_document_raw constructs and closes an AsyncClient."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": {"numFound": 1, "docs": [{"id": "123"}]}}

    created_client = AsyncMock(spec=httpx.AsyncClient)
    created_client.get = AsyncMock(return_value=response)
    created_client.aclose = AsyncMock()

    with patch("okp_mcp.tools.httpx.AsyncClient", return_value=created_client) as client_ctor:
        data = await tools._fetch_document_raw("/solutions/123", solr_endpoint=_SOLR_ENDPOINT)

    client_ctor.assert_called_once_with(timeout=30.0)
    created_client.get.assert_awaited_once()
    created_client.aclose.assert_awaited_once()
    assert data["response"]["numFound"] == 1


async def test_fetch_document_with_query_passes_client_to_solr_query():
    """_fetch_document_with_query forwards explicit client through to _solr_query."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    expected = {"response": {"numFound": 1, "docs": [{"id": "123"}]}}

    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=expected)) as solr_query_mock:
        data = await tools._fetch_document_with_query(
            "/solutions/123", "kernel panic", client=mock_client, solr_endpoint=_SOLR_ENDPOINT
        )

    solr_query_mock.assert_awaited_once()
    assert solr_query_mock.await_args is not None
    kwargs = solr_query_mock.await_args.kwargs
    assert kwargs["client"] is mock_client
    assert kwargs["solr_endpoint"] == _SOLR_ENDPOINT
    assert data == expected


async def test_ctx_is_hidden_from_tool_input_schema():
    """MCP input schemas do not expose internal ctx parameters to clients."""
    tool_names = {
        "search_portal",
        "get_document",
        "run_code",
    }
    listed_tools = await mcp._list_tools()
    target_tools = [tool for tool in listed_tools if tool.name in tool_names]

    assert len(target_tools) == len(tool_names)
    for tool in target_tools:
        properties = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])
        assert "ctx" not in properties
        assert "ctx" not in required


# --- _format_document budget tests ---


async def test_format_document_budget_truncates_large_content():
    """_format_document truncates output to max_chars when content exceeds budget."""
    paragraph = "kernel panic error trace dump occurs during boot sequence\n\n"
    huge_content = paragraph * 2000
    doc = {
        "allTitle": "Test Doc",
        "documentKind": "documentation",
        "main_content": huge_content,
        "view_uri": "/test-doc",
    }
    data = {"highlighting": {}}
    result = await _format_document(doc, data, "/test-doc", "kernel panic", max_chars=200)
    assert len(result) <= 400  # slack for truncation message
    assert "Content truncated" in result
