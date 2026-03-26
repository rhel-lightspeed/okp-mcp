"""Lexical search for the portal-rag Solr core via the /select handler."""

import httpx

from .common import rag_query
from .models import RagResponse


async def lexical_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
) -> RagResponse:
    """Run a lexical search against the portal-rag /select handler.

    Uses basic eDisMax with title and chunk field boosts. Filters to chunk
    documents only (is_chunk:true).

    Args:
        query: Search query string.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8983').
        max_results: Maximum number of results to return (default 10).

    Returns:
        RagResponse with matching document chunks.
    """
    endpoint = f"{solr_url}/solr/portal-rag/select"
    params = {
        "q": query,
        "defType": "edismax",
        "qf": "title^20 chunk^10",
        "rows": max_results,
        "fq": "is_chunk:true",
    }
    return await rag_query(endpoint, params, client)
