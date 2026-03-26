"""Hybrid search for the portal-rag Solr core via the /hybrid-search handler."""

import httpx

from .common import rag_query
from .models import RagResponse


async def hybrid_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
) -> RagResponse:
    """Run a boosted lexical search against the portal-rag /hybrid-search handler.

    The /hybrid-search Solr handler has server-side eDisMax configuration with
    field boosts (title^30, chunk^20, headings_txt^15), phrase boosting, recency
    bias, and document-type weighting. Only q, rows, and fq are sent from the
    client.

    Args:
        query: Search query string.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8983').
        max_results: Maximum number of results to return (default 10).

    Returns:
        RagResponse with matching document chunks.
    """
    endpoint = f"{solr_url}/solr/portal-rag/hybrid-search"
    params = {
        "q": query,
        "rows": max_results,
        "fq": "is_chunk:true",
    }
    return await rag_query(endpoint, params, client)
