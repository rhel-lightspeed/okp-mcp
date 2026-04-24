"""FastMCP server instance."""

# pyright: reportAttributeAccessIssue=false

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastmcp import Context, FastMCP

from okp_mcp.config import ServerConfig
from okp_mcp.request_id import RequestIDContextMiddleware

logger = logging.getLogger(__name__)

_server_config: ServerConfig | None = None


@dataclass
class AppContext:
    """Typed wrapper for lifespan context shared across tools."""

    http_client: httpx.AsyncClient
    solr_endpoint: str
    max_response_chars: int


@asynccontextmanager
async def _app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, AppContext]]:
    """Manage app lifecycle resources for tool execution."""
    del server
    cfg = _server_config if _server_config is not None else ServerConfig()
    solr_endpoint = cfg.solr_endpoint
    max_response_chars = cfg.max_response_chars
    logger.info("SOLR endpoint: %s", solr_endpoint)
    client = httpx.AsyncClient(timeout=30.0)
    try:
        yield {
            "app": AppContext(
                http_client=client,
                solr_endpoint=solr_endpoint,
                max_response_chars=max_response_chars,
            )
        }
    finally:
        await client.aclose()


def get_app_context(ctx: Context) -> AppContext:
    """Return typed application context from lifespan context."""
    return ctx.lifespan_context["app"]


mcp = FastMCP(
    "RHEL OKP Knowledge Base",
    instructions="Search the Red Hat documentation, CVEs, errata, solutions, and articles to answer RHEL questions.",
    lifespan=_app_lifespan,
    middleware=[RequestIDContextMiddleware()],
)
