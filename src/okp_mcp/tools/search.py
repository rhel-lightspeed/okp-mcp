"""Portal search MCP tool."""

import httpx
from fastmcp import Context

from ..config import logger
from ..portal import _format_portal_results, _run_portal_search
from ..server import get_app_context, mcp


@mcp.tool
async def search_portal(
    ctx: Context,
    query: str,
    max_results: int = 10,
) -> str:
    """Search Red Hat knowledge base: documentation, solutions, articles, CVEs, errata, and support policies.

    Use this tool when you need official Red Hat documentation, security advisories,
    or errata to answer accurately, especially for version-specific details,
    lifecycle dates, deprecation status, or changes after 2024. For well-known
    Linux concepts (e.g. vi commands, systemd units, common CLI tools) you may
    answer directly without searching.

    IMPORTANT - interpreting results:
    - Results marked 'Applicability: RHV only' apply to Red Hat Virtualization,
      NOT to standard RHEL KVM. Do not recommend RHV-only workarounds as the
      primary answer for RHEL questions.
    - If results conflict and one states a feature was deprecated or removed in
      RHEL, lead with the deprecation/removal. Mention other-product workarounds
      only as a brief secondary note.
    - When results list specific releases or dates (e.g. EUS availability per
      minor release), enumerate every release explicitly in your answer.
    """
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info("search_portal: query=%r max_results=%d", query, max_results)
    try:
        app = get_app_context(ctx)
        chunks, has_deprecation = await _run_portal_search(
            query,
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
            max_results=max_results,
        )
        return _format_portal_results(chunks, has_deprecation, query, app.max_response_chars)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query, exc_info=True)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.exception("Search failed for query: %r", query)
        return "No results found. The knowledge base may be temporarily unavailable."
