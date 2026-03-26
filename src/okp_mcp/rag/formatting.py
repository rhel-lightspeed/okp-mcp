"""Deduplication and formatting utilities for RAG search results."""

from .models import RagDocument

# Indexed doc: original position paired with the document for rank-order tracking.
_IndexedDoc = tuple[int, RagDocument]


def _chunk_sort_key(item: _IndexedDoc) -> tuple[int, int]:
    """Sort key preferring lowest input position, then highest chunk_index."""
    idx, doc = item
    return (idx, -(doc.chunk_index or 0))


def _passes_token_threshold(doc: RagDocument, min_tokens: int) -> bool:
    """Return True if the chunk meets the minimum token count (None counts as passing)."""
    return doc.num_tokens is None or doc.num_tokens >= min_tokens


def _partition_by_parent(docs: list[RagDocument]) -> tuple[list[_IndexedDoc], dict[str, list[_IndexedDoc]]]:
    """Split docs into orphans (no parent_id) and groups keyed by parent_id."""
    orphans: list[_IndexedDoc] = []
    groups: dict[str, list[_IndexedDoc]] = {}
    for idx, doc in enumerate(docs):
        if doc.parent_id is None:
            orphans.append((idx, doc))
        else:
            groups.setdefault(doc.parent_id, []).append((idx, doc))
    return orphans, groups


def _select_best_per_group(groups: dict[str, list[_IndexedDoc]], min_tokens: int) -> list[_IndexedDoc]:
    """Pick the top-ranked chunk from each parent group, filtering short chunks first."""
    selected: list[_IndexedDoc] = []
    for group in groups.values():
        filtered = [item for item in group if _passes_token_threshold(item[1], min_tokens)]
        candidates = filtered or group
        selected.append(min(candidates, key=_chunk_sort_key))
    return selected


def deduplicate_chunks(
    docs: list[RagDocument],
    *,
    min_tokens: int = 30,
) -> list[RagDocument]:
    """Select the top chunk per parent document and filter short chunks.

    Groups chunks by parent_id and keeps the highest-ranked chunk per parent.
    Chunks with fewer than min_tokens are dropped before selection (unless all
    chunks for a parent are below the threshold, in which case the best one is
    kept). Chunks with None parent_id are each treated as unique and always kept.

    The "highest-ranked" chunk is the one that appears earliest in the input
    list (preserving the rank order from Solr or RRF). When two chunks from the
    same parent have the same input position (shouldn't happen, but defensive),
    prefer chunk_index > 0 over chunk_index == 0 as a tiebreaker.

    Args:
        docs: List of RagDocument chunks, ordered by relevance.
        min_tokens: Minimum token count threshold. Chunks below this are
            dropped unless they're the only chunk for a parent.

    Returns:
        Deduplicated list preserving the original rank order.
    """
    if not docs:
        return []

    orphans, groups = _partition_by_parent(docs)
    selected = _select_best_per_group(groups, min_tokens)
    merged = orphans + selected
    merged.sort(key=lambda x: x[0])
    return [doc for _, doc in merged]


def format_rag_result(doc: RagDocument) -> str:
    """Format a RagDocument chunk as a markdown result block.

    Produces a structured text block with title (bold), section headings,
    product info, URL, and chunk content. Lines for missing fields are
    omitted entirely (no "None" or empty lines).

    Args:
        doc: RagDocument chunk to format.

    Returns:
        Formatted markdown string for LLM consumption.
    """
    lines: list[str] = []

    # Title (always present, bold)
    lines.append(f"**{doc.title}**" if doc.title else "**Untitled**")

    # Section from headings (comma-separated in Solr, display with " > ")
    if doc.headings:
        section = " > ".join(h.strip() for h in doc.headings.split(","))
        lines.append(f"Section: {section}")

    # Product + version
    if doc.product:
        product_str = ", ".join(doc.product)
        if doc.product_version:
            product_str = f"{product_str} {doc.product_version}"
        lines.append(f"Product: {product_str}")

    # URL
    if doc.online_source_url:
        lines.append(f"URL: {doc.online_source_url}")

    # Chunk content (separated by blank line)
    if doc.chunk:
        lines.append("")  # blank line before content
        lines.append(doc.chunk)

    return "\n".join(lines)
