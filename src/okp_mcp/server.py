"""FastMCP server instance."""

# pyright: reportAttributeAccessIssue=false

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastmcp import Context, FastMCP


@dataclass
class AppContext:
    """Typed wrapper for lifespan context shared across tools."""

    http_client: httpx.AsyncClient


@asynccontextmanager
async def _app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, AppContext]]:
    """Manage app lifecycle resources for tool execution."""
    del server
    client = httpx.AsyncClient(timeout=30.0)
    try:
        yield {"app": AppContext(http_client=client)}
    finally:
        await client.aclose()


def get_app_context(ctx: Context) -> AppContext:
    """Return typed application context from lifespan context."""
    return ctx.lifespan_context["app"]


mcp = FastMCP(
    "RHEL OKP Knowledge Base",
    instructions="Search the Red Hat documentation, CVEs, errata, solutions, and articles to answer RHEL questions.",
    lifespan=_app_lifespan,
)
