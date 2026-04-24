"""Portal search MCP tool."""

import logging

import httpx
from fastmcp import Context

from okp_mcp.portal import _MAX_QUERIES, _format_portal_results, _run_multi_query_search, _run_portal_search
from okp_mcp.server import get_app_context, mcp

logger = logging.getLogger("okp_mcp.tools.search_portal")


def _normalize_queries(query: str | list[str]) -> list[str] | str:
    """Validate and normalize the query parameter into a clean list of unique strings.

    Returns a list of stripped, deduplicated query strings on success, or a
    user-facing error string on validation failure.  The caller can
    ``isinstance``-check the return to distinguish the two cases.
    """
    # Validate types.
    if isinstance(query, str):
        raw = [query]
    elif isinstance(query, list):
        bad = [repr(q) for q in query if not isinstance(q, str)]
        if bad:
            return f"All query entries must be strings, got non-string items: {', '.join(bad)}"
        raw = query
    else:
        return f"query must be a string or list of strings, got {type(query).__name__}"

    # Strip whitespace, drop empty entries, and dedupe (preserving order)
    # so identical normalized queries don't inflate RRF votes or waste
    # Solr round-trips.
    seen: set[str] = set()
    cleaned: list[str] = []
    for q in raw:
        stripped = q.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            cleaned.append(stripped)

    return cleaned if cleaned else "Please provide a search query."


@mcp.tool
async def search_portal(
    ctx: Context,
    query: str | list[str],
    # Reduced from 10 to 7 to lower token overhead per search call.
    # 7 results consistently provide enough coverage for the LLM to
    # answer correctly across all functional test scenarios (RSPEED_2480,
    # 2481, 2482).  The LLM can still request up to 20 via the parameter.
    # DO NOT reduce below 5 without testing - some queries need multiple
    # results to cross-reference deprecation vs current status.
    max_results: int = 7,
) -> str:
    """Search Red Hat knowledge base: documentation, solutions, articles, CVEs, errata, and support policies.

    Use this tool when you need official Red Hat documentation, security advisories,
    or errata to answer accurately, especially for version-specific details,
    lifecycle dates, deprecation status, or changes after 2024. For well-known
    Linux concepts (e.g. vi commands, systemd units, common CLI tools) you may
    answer directly without searching.

    Query format: pass a single search string, or a list of up to 3 query
    reformulations for better coverage.  When multiple queries are provided,
    results are merged via reciprocal rank fusion so documents matching
    across multiple phrasings rank higher.

    Multi-query tips:
    - Keep product names and version numbers in every query; never strip
      context to make a query "broader" (e.g. do not reduce "RHEL 8
      container on RHEL 9" to just "container deprecation").
    - Vary the search angle, not the specificity: rephrase using
      RHEL-specific terminology, try official document titles, or use
      different keywords for the same concept.
    - Include the user's original phrasing as one of the queries.
    - For exact lookups (CVE IDs, KB numbers, erratum names), a single
      query is sufficient.
    - Write each query as a complete question or sentence, not bare
      keywords.  Full sentences contain context words (e.g. "virtual
      machines", "deprecated") that significantly improve result quality.
      BAD:  "virt-manager RHEL 9"
      GOOD: "Is virt-manager supported for managing virtual machines in RHEL 9?"

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
    normalized = _normalize_queries(query)
    if isinstance(normalized, str):
        return normalized  # validation error message
    # Cap at _MAX_QUERIES to bound Solr load (research shows diminishing
    # returns past 3 queries).
    queries = normalized[:_MAX_QUERIES]

    max_results = max(1, min(max_results, 20))
    logger.info("search_portal: queries=%r max_results=%d", queries, max_results)

    try:
        app = get_app_context(ctx)

        if len(queries) == 1:
            # Single-query fast path: no behavioral change from before.
            chunks, has_deprecation = await _run_portal_search(
                queries[0],
                client=app.http_client,
                solr_endpoint=app.solr_endpoint,
                max_results=max_results,
            )
        else:
            chunks, has_deprecation = await _run_multi_query_search(
                queries,
                client=app.http_client,
                solr_endpoint=app.solr_endpoint,
                max_results=max_results,
            )

        # Use the first query for the "No results" message and budget selection.
        return _format_portal_results(chunks, has_deprecation, queries[0], app.max_response_chars)
    except httpx.TimeoutException:
        logger.warning("Search timed out for queries: %r", queries, exc_info=True)
        return "The search timed out. Please try again with a simpler query."
    except httpx.HTTPError:
        logger.exception("Search failed for queries: %r", queries)
        return "The knowledge base search is temporarily unavailable. Please try again shortly."
    except ValueError:
        logger.exception("Search failed for queries: %r", queries)
        return "The knowledge base search returned an unexpected response. Please try again."
