"""Deduplication and formatting utilities for RAG search results."""

from .models import RagDocument


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

    none_parent_docs = []
    parent_groups: dict[str, list[tuple[int, RagDocument]]] = {}

    for idx, doc in enumerate(docs):
        if doc.parent_id is None:
            none_parent_docs.append((idx, doc))
        else:
            if doc.parent_id not in parent_groups:
                parent_groups[doc.parent_id] = []
            parent_groups[doc.parent_id].append((idx, doc))

    selected = []

    for parent_id, group in parent_groups.items():
        filtered = [(idx, doc) for idx, doc in group if doc.num_tokens is None or doc.num_tokens >= min_tokens]

        if not filtered:
            filtered = group

        best_idx, best_doc = min(filtered, key=lambda x: (x[0], -(x[1].chunk_index or 0)))
        selected.append((best_idx, best_doc))

    all_selected = none_parent_docs + selected
    all_selected.sort(key=lambda x: x[0])

    return [doc for _, doc in all_selected]


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
