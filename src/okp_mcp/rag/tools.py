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
from .models import RagDocument, RagResponse
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
    """Run hybrid and semantic search in parallel, merge via reciprocal rank fusion.

    Hybrid search uses the cleaned (stopword-stripped) query for BM25 precision.
    Semantic search uses the raw query for natural-language embedding quality.
    If semantic fails, logs a warning and falls back to hybrid results only.
    If hybrid fails, re-raises immediately (hybrid is the primary path).

    Args:
        query: Original raw user query (for semantic search).
        cleaned: Stopword-stripped query (for hybrid BM25 search).
        app: Application context with HTTP client, Solr URL, and embedder.
        max_results: Row count to request from each search backend.
        product: Product filter/boost string.

    Returns:
        RagResponse with merged, RRF-scored results (or hybrid-only on semantic failure).
    """
    if app.embedder is None:
        raise ValueError("Embedder is required for fused search")

    hybrid_coro = hybrid_search(
        cleaned,
        client=app.http_client,
        solr_url=app.rag_solr_url,
        max_results=max_results,
        fl=RAG_FL,
        product=product,
    )
    semantic_coro = semantic_text_search(
        query,
        embedder=app.embedder,
        client=app.http_client,
        solr_url=app.rag_solr_url,
        max_results=max_results,
        fl=RAG_FL,
    )
    hybrid_result, semantic_result = await asyncio.gather(hybrid_coro, semantic_coro, return_exceptions=True)
    if isinstance(hybrid_result, Exception):
        raise hybrid_result
    if isinstance(semantic_result, Exception):
        logger.warning("Semantic search failed, using hybrid results only", exc_info=semantic_result)
        return cast(RagResponse, hybrid_result)
    return reciprocal_rank_fusion(cast(RagResponse, hybrid_result), cast(RagResponse, semantic_result))


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

    Coverage gaps: Does not include Red Hat solutions or articles. For
    troubleshooting guides, use search_documentation or search_solutions instead.
    Graceful degradation and context expansion: semantic failures fall back to
    hybrid-only retrieval, and all results are context-expanded before formatting.

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
        if app.embedder is not None:
            response = await _run_fused_search(query, cleaned, app=app, max_results=max_results * 3, product=product)
        else:
            response = await hybrid_search(
                cleaned,
                client=app.http_client,
                solr_url=app.rag_solr_url,
                max_results=max_results * 3,
                fl=RAG_FL,
                product=product,
            )

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
