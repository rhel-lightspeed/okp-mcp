"""Hybrid search for the portal-rag Solr core via the /hybrid-search handler."""

import httpx

from .common import rag_query
from .models import RagResponse

_PRODUCT_ALIASES: dict[str, str] = {
    "RHEL": "Red Hat Enterprise Linux",
    "OCP": "Red Hat OpenShift Container Platform",
}


async def hybrid_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
    fl: str | None = None,
    product: str | None = None,
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
        fl: Field list to return from Solr (optional). If None, Solr defaults are used.
        product: Product name to boost results (optional). Supports aliases: RHEL, OCP.
            Empty string defaults to "Red Hat Enterprise Linux". If None, no boost applied.

    Returns:
        RagResponse with matching document chunks.
    """
    endpoint = f"{solr_url}/solr/portal-rag/hybrid-search"
    params = {
        "q": query,
        "rows": max_results,
        "fq": "is_chunk:true",
    }
    if fl is not None:
        params["fl"] = fl
    if product is not None:
        normalized_product = _PRODUCT_ALIASES.get(product, product) or "Red Hat Enterprise Linux"
        # Strip double quotes and backslashes to prevent Solr query injection
        # and unterminated queries (trailing \ escapes the closing quote)
        normalized_product = normalized_product.replace("\\", "").replace('"', "")
        params["bq"] = f'product:("{normalized_product}")^10'
    return await rag_query(endpoint, params, client)
