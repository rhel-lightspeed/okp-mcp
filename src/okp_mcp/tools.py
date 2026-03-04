"""MCP tool definitions for OKP knowledge base search."""

import logging

import httpx

from .config import ServerConfig
from .server import mcp

logger = logging.getLogger(__name__)


@mcp.tool
async def solr_query(query: str, max_results: int = 5) -> str:
    """Execute a raw Solr query against the OKP knowledge base.

    Pass a Solr query string (edismax syntax) and get back matching documents
    with titles, URLs, and content snippets. The query runs against the
    ``portal`` collection and searches across title, heading, and content fields.

    This is a demo tool. Use it to explore the knowledge base before
    purpose-built search tools are available.
    """
    if not query or not query.strip():
        return "Please provide a search query."

    max_results = max(1, min(max_results, 20))
    config = ServerConfig()

    params = {
        "q": query,
        "wt": "json",
        "defType": "edismax",
        "qf": "title^5 main_content heading_h1^3 content^2",
        "fl": "allTitle,title,view_uri,documentKind,product,score",
        "rows": max_results,
    }

    logger.info("solr_query: q=%r max_results=%d endpoint=%s", query, max_results, config.solr_endpoint)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(config.solr_endpoint, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        logger.warning("Solr query timed out: %r", query)
        return "The search timed out. Please try a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.exception("Solr query failed: %r", query)
        return "Search failed. The knowledge base may be temporarily unavailable."

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        return f"No results found for: {query}"

    results = []
    for doc in docs:
        title = doc.get("allTitle") or doc.get("title", "Untitled")
        kind = doc.get("documentKind", "unknown")
        url = doc.get("view_uri", "")
        entry = f"**{title}**\nType: {kind}"
        if doc.get("product"):
            entry += f" | Product: {doc['product']}"
        if url:
            entry += f"\nURL: https://access.redhat.com{url}"
        results.append(entry)

    return f"Found {len(docs)} result(s) for '{query}':\n\n" + "\n\n---\n\n".join(results)
