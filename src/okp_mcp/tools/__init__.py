"""MCP tool definitions for RHEL OKP knowledge base search."""

from okp_mcp.tools.document import get_document as get_document
from okp_mcp.tools.run_code import run_code as run_code
from okp_mcp.tools.search import search_portal as search_portal


# Tool functions that will be registered with the MCP server.
# All tools need to be listed here or they will not be registered.
__all__ = [
    "get_document",
    "run_code",
    "search_portal",
]
