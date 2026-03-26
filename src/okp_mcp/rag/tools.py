"""MCP tool definitions for RAG search against the portal-rag Solr core."""

import logging

import httpx
from fastmcp import Context

from ..server import get_app_context, mcp
from .common import RAG_FL, clean_rag_query
from .formatting import deduplicate_chunks, format_rag_result
from .hybrid import hybrid_search
from .models import RagDocument

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


@mcp.tool(tags={"rag"})
async def search_rag(
    ctx: Context,
    query: str,
    product: str = "",
    max_results: int = 10,
) -> str:
    """Search Red Hat documentation, CVEs, and errata using semantic-boosted search.

    Uses the portal-rag knowledge base, which provides higher-fidelity chunks
    from Red Hat documentation, CVEs, and errata than the portal search tools.

    Coverage gaps: Does not include Red Hat solutions or articles. For
    troubleshooting guides, use search_documentation or search_solutions instead.

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
        return _assemble_rag_output(deduped, query, app.max_response_chars)

    except httpx.TimeoutException:
        logger.warning("search_rag timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("search_rag failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."
