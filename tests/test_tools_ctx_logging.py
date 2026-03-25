"""Tests verifying MCP protocol structured logging via Context in tool functions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import okp_mcp  # noqa: F401 -- triggers @mcp.tool registration
from okp_mcp import tools
from okp_mcp.server import AppContext

_EMPTY_SOLR = {
    "responseHeader": {"status": 0, "QTime": 1},
    "response": {"numFound": 0, "docs": []},
}


@pytest.fixture
def mock_ctx():
    """Minimal ctx mock with async logging methods and a wired-up AppContext."""
    app = AppContext(
        http_client=AsyncMock(),
        solr_endpoint="http://localhost:8983/solr/portal/select",
        max_response_chars=30_000,
    )
    ctx = SimpleNamespace(
        lifespan_context={"app": app},
        info=AsyncMock(),
        warning=AsyncMock(),
        error=AsyncMock(),
        report_progress=AsyncMock(),
    )
    return ctx


async def test_search_documentation_logs_ctx_info_on_entry(mock_ctx):
    """search_documentation emits ctx.info when query is provided."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_documentation(mock_ctx, "test query")
    mock_ctx.info.assert_awaited_once()
    assert "test query" in mock_ctx.info.call_args[0][0]


async def test_search_documentation_skips_ctx_on_empty_query(mock_ctx):
    """search_documentation returns early on empty query without calling ctx.info."""
    result = await tools.search_documentation(mock_ctx, "")
    assert result == "Please provide a search query."
    mock_ctx.info.assert_not_awaited()


async def test_search_documentation_logs_ctx_warning_on_timeout(mock_ctx):
    """search_documentation emits ctx.warning on TimeoutException."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
        result = await tools.search_documentation(mock_ctx, "test query")
    assert "timed out" in result.lower()
    mock_ctx.warning.assert_awaited_once()
    assert "timed out" in mock_ctx.warning.call_args[0][0].lower()


@pytest.mark.parametrize("exc", [httpx.HTTPError("fail"), ValueError("bad")])
async def test_search_documentation_logs_ctx_error_on_failure(mock_ctx, exc):
    """search_documentation emits ctx.error on HTTPError or ValueError."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(side_effect=exc)):
        result = await tools.search_documentation(mock_ctx, "test query")
    assert "unavailable" in result.lower()
    mock_ctx.error.assert_awaited_once()
    assert "unavailable" in mock_ctx.error.call_args[0][0].lower()


async def test_search_solutions_logs_ctx_info_on_entry(mock_ctx):
    """search_solutions emits ctx.info when query is provided."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_solutions(mock_ctx, "test query")
    mock_ctx.info.assert_awaited_once()
    assert "test query" in mock_ctx.info.call_args[0][0]


async def test_search_solutions_logs_ctx_warning_on_timeout(mock_ctx):
    """search_solutions emits ctx.warning on TimeoutException."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
        await tools.search_solutions(mock_ctx, "test query")
    mock_ctx.warning.assert_awaited_once()


async def test_search_solutions_logs_ctx_error_on_failure(mock_ctx):
    """search_solutions emits ctx.error on HTTPError."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(side_effect=httpx.HTTPError("fail"))):
        await tools.search_solutions(mock_ctx, "test query")
    mock_ctx.error.assert_awaited_once()


async def test_search_cves_logs_ctx_info_on_entry(mock_ctx):
    """search_cves emits ctx.info when query is provided."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_cves(mock_ctx, "test query")
    mock_ctx.info.assert_awaited_once()
    assert "test query" in mock_ctx.info.call_args[0][0]


async def test_search_errata_logs_ctx_info_on_entry(mock_ctx):
    """search_errata emits ctx.info when query is provided."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_errata(mock_ctx, "test query")
    mock_ctx.info.assert_awaited_once()
    assert "test query" in mock_ctx.info.call_args[0][0]


async def test_search_articles_logs_ctx_info_on_entry(mock_ctx):
    """search_articles emits ctx.info when query is provided."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_articles(mock_ctx, "test query")
    mock_ctx.info.assert_awaited_once()
    assert "test query" in mock_ctx.info.call_args[0][0]


async def test_get_document_logs_ctx_info_on_entry(mock_ctx):
    """get_document emits ctx.info with doc_id."""
    solr_resp = {
        "responseHeader": {"status": 0, "QTime": 1},
        "response": {
            "numFound": 1,
            "docs": [{"allTitle": "Test", "view_uri": "/docs/test", "documentKind": "documentation"}],
        },
    }
    with patch("okp_mcp.tools._fetch_document_raw", AsyncMock(return_value=solr_resp)):
        await tools.get_document(mock_ctx, "/docs/test")
    mock_ctx.info.assert_awaited_once()
    assert "/docs/test" in mock_ctx.info.call_args[0][0]


async def test_get_document_logs_ctx_warning_on_timeout(mock_ctx):
    """get_document emits ctx.warning with document-specific message on timeout."""
    with patch("okp_mcp.tools._fetch_document_raw", AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
        await tools.get_document(mock_ctx, "/docs/test")
    mock_ctx.warning.assert_awaited_once()
    assert "document" in mock_ctx.warning.call_args[0][0].lower()


async def test_get_document_logs_ctx_error_on_failure(mock_ctx):
    """get_document emits ctx.error with document-specific message on failure."""
    with patch("okp_mcp.tools._fetch_document_raw", AsyncMock(side_effect=httpx.HTTPError("fail"))):
        await tools.get_document(mock_ctx, "/docs/test")
    mock_ctx.error.assert_awaited_once()
    assert "document" in mock_ctx.error.call_args[0][0].lower()


async def test_run_code_logs_ctx_info(mock_ctx):
    """run_code emits ctx.info about code execution being unsupported."""
    result = await tools.run_code(mock_ctx, "python", "print('hi')")
    assert "not available" in result.lower()
    mock_ctx.info.assert_awaited_once()
    assert "not supported" in mock_ctx.info.call_args[0][0].lower()


async def test_search_documentation_reports_progress(mock_ctx):
    """search_documentation calls report_progress twice (start and end)."""
    with patch("okp_mcp.tools._solr_query", AsyncMock(return_value=_EMPTY_SOLR)):
        await tools.search_documentation(mock_ctx, "test query")
    assert mock_ctx.report_progress.await_count == 2


async def test_get_document_reports_progress(mock_ctx):
    """get_document calls report_progress twice."""
    solr_resp = {
        "responseHeader": {"status": 0, "QTime": 1},
        "response": {
            "numFound": 1,
            "docs": [{"allTitle": "Test", "view_uri": "/docs/test", "documentKind": "documentation"}],
        },
    }
    with patch("okp_mcp.tools._fetch_document_raw", AsyncMock(return_value=solr_resp)):
        await tools.get_document(mock_ctx, "/docs/test")
    assert mock_ctx.report_progress.await_count == 2
