"""Tests for tool context parameter and shared HTTP client wiring."""

# pyright: reportMissingImports=false

import inspect
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import okp_mcp  # noqa: F401 -- triggers @mcp.tool registration
from okp_mcp import tools
from okp_mcp.config import ServerConfig
from okp_mcp.server import mcp
from okp_mcp.tools import _doc_id_filter, _escape_solr_phrase, _format_document, _normalize_doc_id
from okp_mcp.tools import document as document_tools

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

    with patch(
        "okp_mcp.tools.document.httpx.AsyncClient", side_effect=AssertionError("constructor should not be called")
    ):
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

    with patch("okp_mcp.tools.document.httpx.AsyncClient", return_value=created_client) as client_ctor:
        data = await tools._fetch_document_raw("/solutions/123", solr_endpoint=_SOLR_ENDPOINT)

    client_ctor.assert_called_once_with(timeout=30.0)
    created_client.get.assert_awaited_once()
    created_client.aclose.assert_awaited_once()
    assert data["response"]["numFound"] == 1


async def test_fetch_document_with_query_passes_client_to_solr_query():
    """_fetch_document_with_query forwards explicit client through to _solr_query."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    expected = {"response": {"numFound": 1, "docs": [{"id": "123"}]}}

    with patch("okp_mcp.tools.document._solr_query", AsyncMock(return_value=expected)) as solr_query_mock:
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


# --- _normalize_doc_id tests ---


@pytest.mark.parametrize(
    ("doc_id", "expected"),
    [
        pytest.param(
            "https://access.redhat.com/documentation/en-us/rhel/9/html/configuring_networking/index",
            "/documentation/en-us/rhel/9/html/configuring_networking/index",
            id="full_url_stripped",
        ),
        pytest.param(
            "http://access.redhat.com/solutions/12345",
            "/solutions/12345",
            id="http_url_stripped",
        ),
        pytest.param(
            "https://access.redhat.com/docs/page?foo=bar#section",
            "/docs/page",
            id="query_and_fragment_stripped",
        ),
        pytest.param(
            "/documentation/en-us/rhel/9/html/configuring_networking/index",
            "/documentation/en-us/rhel/9/html/configuring_networking/index",
            id="path_unchanged",
        ),
        pytest.param("RHSA-2022:4915", "RHSA-2022:4915", id="errata_id_unchanged"),
        pytest.param(
            "https://example.com/docs/page",
            "https://example.com/docs/page",
            id="other_domain_unchanged",
        ),
        pytest.param(
            "https://access.redhat.com.evil.tld/phish",
            "https://access.redhat.com.evil.tld/phish",
            id="lookalike_domain_rejected",
        ),
    ],
)
def test_normalize_doc_id(doc_id: str, expected: str):
    """_normalize_doc_id correctly strips access.redhat.com URLs and rejects imposters."""
    assert _normalize_doc_id(doc_id) == expected


# --- _escape_solr_phrase tests ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("simple", "simple", id="plain_text"),
        pytest.param('has"quote', 'has\\"quote', id="double_quote_escaped"),
        pytest.param("has\\backslash", "has\\\\backslash", id="backslash_escaped"),
        pytest.param('both\\"chars', 'both\\\\\\"chars', id="both_escaped"),
    ],
)
def test_escape_solr_phrase(value: str, expected: str):
    """_escape_solr_phrase escapes backslashes and double quotes for Lucene."""
    assert _escape_solr_phrase(value) == expected


# --- _doc_id_filter tests ---


@pytest.mark.parametrize(
    ("doc_id", "expected_id", "expected_view_uri"),
    [
        pytest.param(
            "/documentation/en-us/rhel/9",
            'id:"/documentation/en-us/rhel/9"',
            'view_uri:"/documentation/en-us/rhel/9"',
            id="path_filter",
        ),
        pytest.param(
            "RHSA-2022:4915",
            'id:"RHSA-2022:4915"',
            'view_uri:"RHSA-2022:4915"',
            id="errata_filter",
        ),
        pytest.param(
            'inject"attempt',
            'id:"inject\\"attempt"',
            'view_uri:"inject\\"attempt"',
            id="quote_escaped",
        ),
    ],
)
def test_doc_id_filter(doc_id: str, expected_id: str, expected_view_uri: str):
    """_doc_id_filter produces an OR filter with properly escaped values."""
    result = _doc_id_filter(doc_id)
    assert expected_id in result
    assert expected_view_uri in result
    assert " OR " in result


# --- get_document tool-level integration test ---


async def test_get_document_normalizes_full_url():
    """get_document accepts a full access.redhat.com URL and normalizes it before querying Solr."""
    full_url = "https://access.redhat.com/documentation/en-us/rhel/9/html/configuring_networking/index"
    expected_path = "/documentation/en-us/rhel/9/html/configuring_networking/index"

    mock_ctx = Mock()
    mock_app = Mock()
    mock_app.http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_app.solr_endpoint = _SOLR_ENDPOINT
    mock_app.max_response_chars = 5000

    with (
        patch("okp_mcp.tools.document.get_app_context", return_value=mock_app),
        patch("okp_mcp.tools.document._fetch_document_raw", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_fetch.return_value = {"response": {"docs": [{"allTitle": "Test", "documentKind": "documentation"}]}}
        await document_tools.get_document(mock_ctx, full_url)

        # The normalized path (not the full URL) should reach the fetch function.
        call_args = mock_fetch.call_args
        assert call_args[0][0] == expected_path
