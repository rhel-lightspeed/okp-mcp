"""Tests for FastMCP instance configuration and tool registration."""

import pytest

from okp_mcp.server import mcp


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
