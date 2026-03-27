"""MCP tool definitions for RAG search against the portal-rag Solr core."""

import asyncio
import logging
from typing import cast

import httpx
from fastmcp import Context

from ..server import AppContext, get_app_context, mcp
from .common import RAG_FL, clean_rag_query
from .context import expand_chunks
from .formatting import deduplicate_chunks, format_rag_result
from .hybrid import hybrid_search
from .models import PortalResponse, RagDocument, RagResponse
from .portal import PORTAL_FL, portal_highlights_to_rag_results, portal_search
from .rrf import reciprocal_rank_fusion
from .semantic import semantic_text_search

logger = logging.getLogger(__name__)


def _assemble_rag_output(docs: list[RagDocument], query: str, max_chars: int) -> str:
    """Format deduplicated RAG docs into a budget-constrained response string.

    Always includes at least one result even if it exceeds the character budget.
    Appends a truncation notice when results are cut short.

    Args:
        docs: Deduplicated RagDocument list, already sliced to max_results.
        query: Original user query (for the response header).
        max_chars: Maximum response character budget.

    Returns:
        Formatted multi-result string with header and separator lines.
    """
    parts: list[str] = []
    char_count = 0
    for doc in docs:
        formatted = format_rag_result(doc)
        if parts and char_count + len(formatted) > max_chars:
            parts.append(f"(Showing {len(parts)} of {len(docs)} results; response size limit reached)")
            break
        parts.append(formatted)
        char_count += len(formatted)

    n_shown = len(parts)
    if parts and parts[-1].startswith("(Showing"):
        n_shown -= 1

    return f"Found {n_shown} results for '{query}':\n\n" + "\n\n---\n\n".join(parts)


async def _run_fused_search(
    query: str,
    cleaned: str,
    *,
    app: AppContext,
    max_results: int,
    product: str,
) -> RagResponse:
    """Run hybrid, semantic (if available), and portal search in parallel, merged via RRF.

    Hybrid search uses the cleaned (stopword-stripped) query for BM25 precision.
    Semantic search uses the raw query for natural-language embedding quality.
    Portal search uses the cleaned query (same as hybrid, since it uses eDisMax BM25).
    Semantic and portal failures are supplementary (warning + exclude from fusion).
    If hybrid fails, re-raises immediately (hybrid is the primary path).
    When embedder is None, runs hybrid + portal only.

    Args:
        query: Original raw user query (for semantic search).
        cleaned: Stopword-stripped query (for hybrid BM25 search and portal search).
        app: Application context with HTTP client, Solr URL, and embedder.
        max_results: Row count to request from each search backend.
        product: Product filter/boost string.

    Returns:
        RagResponse with merged, RRF-scored results.
    """
    hybrid_coro = hybrid_search(
        cleaned,
        client=app.http_client,
        solr_url=app.rag_solr_url,
        max_results=max_results,
        fl=RAG_FL,
        product=product,
    )
    portal_coro = portal_search(
        cleaned,
        client=app.http_client,
        solr_url=app.rag_solr_url,
        max_results=max_results,
        fl=PORTAL_FL,
    )

    if app.embedder is not None:
        semantic_coro = semantic_text_search(
            query,
            embedder=app.embedder,
            client=app.http_client,
            solr_url=app.rag_solr_url,
            max_results=max_results,
            fl=RAG_FL,
        )
        hybrid_result, semantic_result, portal_result = await asyncio.gather(
            hybrid_coro, semantic_coro, portal_coro, return_exceptions=True
        )
    else:
        hybrid_result, portal_result = await asyncio.gather(hybrid_coro, portal_coro, return_exceptions=True)
        semantic_result = None

    if isinstance(hybrid_result, Exception):
        raise hybrid_result

    rag_results: list[RagResponse] = [cast(RagResponse, hybrid_result)]

    if isinstance(semantic_result, Exception):
        logger.warning("Semantic search failed, excluding from fusion", exc_info=semantic_result)
    elif semantic_result is not None:
        rag_results.append(cast(RagResponse, semantic_result))

    if isinstance(portal_result, Exception):
        logger.warning("Portal search failed, excluding from fusion", exc_info=portal_result)
    else:
        portal_rag = portal_highlights_to_rag_results(cast(PortalResponse, portal_result))
        if portal_rag.docs:
            rag_results.append(portal_rag)

    return reciprocal_rank_fusion(*rag_results)


@mcp.tool(tags={"rag"})
async def search_rag(
    ctx: Context,
    query: str,
    product: str = "",
    max_results: int = 10,
) -> str:
    """Search Red Hat documentation, CVEs, and errata using fused lexical and semantic retrieval.

    Uses the portal-rag knowledge base, which provides higher-fidelity chunks
    from Red Hat documentation, CVEs, and errata than the portal search tools.

    Also searches Red Hat solutions and articles from the portal core,
    fused with documentation results via reciprocal rank fusion.
    Graceful degradation: semantic or portal failures are excluded from fusion
    (warning logged), and all results are context-expanded before formatting.

    When to prefer this tool:
    - Looking up CVE details, errata advisories, or documentation excerpts
    - Need precise, chunk-level results from official Red Hat docs
    - Searching for RHEL configuration procedures or feature descriptions

    Args:
        query: Search query describing what you need.
        product: Product name to boost results (e.g., "RHEL", "OCP", or
            "Red Hat Enterprise Linux"). Defaults to RHEL boost.
        max_results: Maximum number of results (1-20, default 10).
    """
    if not query or not query.strip():
        return "Please provide a search query."

    max_results = max(1, min(max_results, 20))
    logger.info("search_rag: query=%r product=%r max_results=%d", query, product, max_results)

    try:
        app = get_app_context(ctx)
        cleaned = clean_rag_query(query)
        response = await _run_fused_search(query, cleaned, app=app, max_results=max_results * 3, product=product)

        if not response.docs:
            return f"No results found for: {query}"

        deduped = deduplicate_chunks(response.docs)[:max_results]
        expanded = await expand_chunks(deduped, client=app.http_client, solr_url=app.rag_solr_url)
        return _assemble_rag_output(expanded, query, app.max_response_chars)

    except httpx.TimeoutException:
        logger.warning("search_rag timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("search_rag failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."
