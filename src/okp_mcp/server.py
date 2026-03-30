"""FastMCP server instance."""

# pyright: reportAttributeAccessIssue=false

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastmcp import Context, FastMCP

from .config import ServerConfig
from .rag.embeddings import Embedder

logger = logging.getLogger(__name__)

_server_config: ServerConfig | None = None


@dataclass
class AppContext:
    """Typed wrapper for lifespan context shared across tools."""

    http_client: httpx.AsyncClient
    solr_endpoint: str
    max_response_chars: int
    rag_solr_url: str
    embedder: Embedder | None = None


@asynccontextmanager
async def _app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, AppContext]]:
    """Manage app lifecycle resources for tool execution."""
    del server
    cfg = _server_config if _server_config is not None else ServerConfig()
    solr_endpoint = cfg.solr_endpoint
    max_response_chars = cfg.max_response_chars
    rag_solr_url = cfg.rag_solr_url or cfg.solr_url
    if cfg.rag_solr_url:
        mcp.disable(tags={"portal"})
        logger.info("Portal search tools disabled: RAG tools active (MCP_RAG_SOLR_URL is set)")
    else:
        logger.warning("MCP_RAG_SOLR_URL not set; falling back to solr_url (%s) for RAG queries", cfg.solr_url)
        mcp.disable(tags={"rag"})
        logger.info("RAG tools disabled: MCP_RAG_SOLR_URL not set")
    logger.info("SOLR endpoint: %s", solr_endpoint)
    logger.info("RAG Solr URL: %s", rag_solr_url)
    embedder = None
    if cfg.rag_solr_url:
        try:
            embedder = Embedder(
                model_name=cfg.embedding_model,
                cache_dir=cfg.embedding_cache_dir or "",
            )
            logger.info("Embedder loaded: %s", cfg.embedding_model)
        except Exception:
            logger.warning("Embedding model unavailable; RAG semantic search disabled", exc_info=True)
    client = httpx.AsyncClient(timeout=30.0)
    try:
        yield {
            "app": AppContext(
                http_client=client,
                solr_endpoint=solr_endpoint,
                max_response_chars=max_response_chars,
                rag_solr_url=rag_solr_url,
                embedder=embedder,
            )
        }
    finally:
        await client.aclose()
        if embedder is not None:
            try:
                embedder.close()
            except Exception:
                logger.warning("Failed to close embedder cleanly", exc_info=True)


def get_app_context(ctx: Context) -> AppContext:
    """Return typed application context from lifespan context."""
    return ctx.lifespan_context["app"]


mcp = FastMCP(
    "RHEL OKP Knowledge Base",
    instructions="Search the Red Hat documentation, CVEs, errata, solutions, and articles to answer RHEL questions.",
    lifespan=_app_lifespan,
)
