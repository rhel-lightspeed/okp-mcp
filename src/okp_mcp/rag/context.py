"""Context expansion utilities for retrieving surrounding RAG chunks."""

import asyncio
import logging

import httpx

from .common import RAG_FL, rag_query
from .models import RagDocument

logger = logging.getLogger(__name__)

PARENT_FL = "doc_id,total_chunks,total_tokens"
DEFAULT_WINDOW = 2
DEFAULT_MAX_TOTAL_TOKENS = 4000


async def fetch_parent_metadata(
    parent_id: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
) -> RagDocument | None:
    """Fetch chunk count and token total for a parent document.

    Queries the portal-rag core for the parent record (is_chunk:false) to
    retrieve total_chunks and total_tokens, which drive the expand decision.

    Args:
        parent_id: The parent document's doc_id.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8984').

    Returns:
        RagDocument with total_chunks and total_tokens, or None if not found.
    """
    endpoint = f"{solr_url}/solr/portal-rag/select"
    params = {
        "q": f"{{!term f=doc_id}}{parent_id}",
        "fq": "is_chunk:false",
        "fl": PARENT_FL,
        "rows": 1,
    }
    response = await rag_query(endpoint, params, client)
    return response.docs[0] if response.docs else None


async def fetch_sibling_chunks(
    parent_id: str,
    chunk_index: int,
    *,
    window: int = DEFAULT_WINDOW,
    client: httpx.AsyncClient,
    solr_url: str,
) -> list[RagDocument]:
    """Fetch a window of chunks around chunk_index from the same parent.

    Retrieves chunks from max(0, chunk_index - window) through
    chunk_index + window, ordered by chunk_index ascending. Useful for
    large documents where full retrieval would exceed token budgets.

    Args:
        parent_id: The parent document's doc_id.
        chunk_index: The 0-based index of the anchor chunk.
        window: Number of chunks to fetch on each side (default 2).
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL.

    Returns:
        List of RagDocument chunks ordered by chunk_index.
    """
    start = max(0, chunk_index - window)
    end = chunk_index + window
    endpoint = f"{solr_url}/solr/portal-rag/select"
    params = {
        "q": "*:*",
        "fq": [
            f"{{!term f=parent_id}}{parent_id}",
            f"chunk_index:[{start} TO {end}]",
        ],
        "sort": "chunk_index asc",
        "rows": 1 + 2 * window,
        "fl": RAG_FL,
    }
    response = await rag_query(endpoint, params, client)
    return response.docs


async def fetch_full_document_chunks(
    parent_id: str,
    *,
    total_chunks: int,
    client: httpx.AsyncClient,
    solr_url: str,
) -> list[RagDocument]:
    """Fetch all chunks for a parent document, ordered by chunk_index.

    Used for small documents where the entire content fits within the
    token budget. The caller should verify total_tokens is acceptable
    before calling this function.

    Args:
        parent_id: The parent document's doc_id.
        total_chunks: Expected number of chunks (from parent metadata).
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL.

    Returns:
        List of all RagDocument chunks ordered by chunk_index.
    """
    endpoint = f"{solr_url}/solr/portal-rag/select"
    params = {
        "q": "*:*",
        "fq": f"{{!term f=parent_id}}{parent_id}",
        "sort": "chunk_index asc",
        "rows": total_chunks,
        "fl": RAG_FL,
    }
    response = await rag_query(endpoint, params, client)
    return response.docs


def merge_chunks(anchor: RagDocument, chunks: list[RagDocument]) -> RagDocument:
    """Merge expanded chunks into a single document, preserving anchor metadata.

    Concatenates chunk texts in chunk_index order with blank-line separators.
    Title, URL, product, headings, and other metadata come from the anchor
    chunk (the originally-matched result). Token count is summed across all
    merged chunks.

    Args:
        anchor: The originally-matched chunk whose metadata is preserved.
        chunks: Ordered list of chunks to merge.

    Returns:
        A single RagDocument with concatenated chunk content.
    """
    if len(chunks) <= 1:
        return anchor
    merged = anchor.model_copy()
    texts = [c.chunk for c in chunks if c.chunk]
    merged.chunk = "\n\n".join(texts)
    merged.num_tokens = sum(c.num_tokens or 0 for c in chunks)
    return merged


async def expand_chunk(
    chunk: RagDocument,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    window: int = DEFAULT_WINDOW,
) -> RagDocument:
    """Expand a single chunk with surrounding context from its parent document.

    For small documents (total_tokens <= max_total_tokens), retrieves and
    merges all chunks from the parent. For large documents, retrieves a
    window of chunks around the matched chunk. Returns the chunk unchanged
    if it has no parent_id, no chunk_index, or if the parent lookup fails.

    Errors during expansion are logged and the original chunk is returned,
    so a failed expansion never breaks the search pipeline.

    Args:
        chunk: The matched chunk to expand.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL.
        max_total_tokens: Maximum parent token count for full-document
            retrieval (default 4000). Documents above this threshold get
            windowed expansion instead.
        window: Number of sibling chunks on each side for windowed
            retrieval (default 2).

    Returns:
        A RagDocument with expanded chunk content, or the original unchanged.
    """
    if not chunk.parent_id or chunk.chunk_index is None:
        return chunk

    try:
        parent = await fetch_parent_metadata(
            chunk.parent_id,
            client=client,
            solr_url=solr_url,
        )
    except httpx.HTTPError:
        logger.warning("Context expansion: failed to fetch parent metadata for %s", chunk.parent_id)
        return chunk

    if not parent:
        return chunk

    try:
        if (
            parent.total_tokens is not None
            and parent.total_tokens <= max_total_tokens
            and parent.total_chunks is not None
        ):
            siblings = await fetch_full_document_chunks(
                chunk.parent_id,
                total_chunks=parent.total_chunks,
                client=client,
                solr_url=solr_url,
            )
        else:
            siblings = await fetch_sibling_chunks(
                chunk.parent_id,
                chunk.chunk_index,
                window=window,
                client=client,
                solr_url=solr_url,
            )
    except httpx.HTTPError:
        logger.warning("Context expansion: failed to fetch chunks for %s", chunk.parent_id)
        return chunk

    return merge_chunks(chunk, siblings) if siblings else chunk


async def expand_chunks(
    chunks: list[RagDocument],
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    window: int = DEFAULT_WINDOW,
) -> list[RagDocument]:
    """Expand all chunks in parallel with surrounding context.

    Each chunk is independently expanded via expand_chunk(). All expansions
    run concurrently using asyncio.gather for minimal latency. Failed
    expansions return the original chunk unchanged.

    Args:
        chunks: Deduplicated list of matched chunks to expand.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL.
        max_total_tokens: Maximum parent token count for full-document
            retrieval (default 4000).
        window: Number of sibling chunks on each side for windowed
            retrieval (default 2).

    Returns:
        List of expanded RagDocuments preserving input order.
    """
    if not chunks:
        return []

    tasks = [
        expand_chunk(
            chunk,
            client=client,
            solr_url=solr_url,
            max_total_tokens=max_total_tokens,
            window=window,
        )
        for chunk in chunks
    ]
    return list(await asyncio.gather(*tasks))
