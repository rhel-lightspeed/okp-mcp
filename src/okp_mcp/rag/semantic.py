"""Semantic (KNN vector) search stub for the portal-rag Solr core.

Accepts pre-computed 384-dimensional vectors and queries the /semantic-search
Solr handler using the {!knn} syntax. Embedding model integration (text to
vector conversion) is future work -- this stub handles only the Solr query
mechanics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from .common import rag_query

if TYPE_CHECKING:
    from .embeddings import Embedder

VECTOR_DIMENSIONS = 384


async def semantic_search(
    vector: list[float],
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
) -> dict:
    """Run a KNN vector search against the portal-rag /semantic-search handler.

    Accepts a pre-computed 384-dimensional embedding vector. The vector must
    match the dimensions of the granite-embedding-30m-english model used to
    index chunks. Text-to-vector conversion is not handled here.

    Args:
        vector: Pre-computed embedding vector of length 384.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8983').
        max_results: Maximum number of results to return (default 10).

    Returns:
        Raw Solr JSON response dict.

    Raises:
        ValueError: If vector length is not 384.
    """
    if len(vector) != VECTOR_DIMENSIONS:
        raise ValueError(f"Vector must have {VECTOR_DIMENSIONS} dimensions, got {len(vector)}")
    vector_str = ",".join(str(v) for v in vector)
    knn_query = f"{{!knn f=chunk_vector topK={max_results}}}[{vector_str}]"
    endpoint = f"{solr_url}/solr/portal-rag/semantic-search"
    params = {
        "q": knn_query,
        "rows": max_results,
        "fq": "is_chunk:true",
    }
    return await rag_query(endpoint, params, client)


async def semantic_text_search(
    text: str,
    *,
    embedder: Embedder,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
) -> dict:
    """Embed text and run KNN semantic search against the portal-rag core.

    Converts text to a 384-dimensional vector using the embedder, then calls
    semantic_search() with the resulting vector.

    Args:
        text: Query text to embed and search.
        embedder: Embedder instance to convert text to vector.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8983').
        max_results: Maximum number of results to return (default 10).

    Returns:
        Raw Solr JSON response dict (same shape as semantic_search).
    """
    vector = await embedder.encode_async(text)
    return await semantic_search(vector, client=client, solr_url=solr_url, max_results=max_results)
