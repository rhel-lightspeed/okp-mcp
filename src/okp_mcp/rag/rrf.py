"""Reciprocal rank fusion for merging multiple Solr result sets."""

import logging

from .models import RagDocument, RagResponse

logger = logging.getLogger(__name__)


def _accumulate_scores(
    docs: list[RagDocument], scores: dict[str, float], doc_map: dict[str, RagDocument], k: int, doc_key: str
) -> None:
    """Accumulate RRF scores and collect doc data from a single result list.

    Args:
        docs: List of RagDocument instances.
        scores: Mutable score accumulator keyed by doc identifier.
        doc_map: Mutable dict collecting the first-seen version of each doc.
        k: RRF constant.
        doc_key: Field name used as the unique document identifier.
    """
    for rank, doc in enumerate(docs):
        identifier = getattr(doc, doc_key, None)
        if not identifier:
            logger.warning("Skipping doc at rank %d missing key %r", rank, doc_key)
            continue
        scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + rank)
        if identifier not in doc_map:
            doc_map[identifier] = doc


def reciprocal_rank_fusion(
    results_a: RagResponse,
    results_b: RagResponse,
    *,
    k: int = 60,
    doc_key: str = "doc_id",
) -> RagResponse:
    """Merge two Solr result sets using reciprocal rank fusion (RRF).

    Combines two result lists by computing RRF scores: for each unique document,
    sums 1/(k + rank) across all lists where it appears (rank is 0-indexed position).
    Documents appearing in both lists score higher than single-list documents.

    Args:
        results_a: First RagResponse to merge.
        results_b: Second RagResponse to merge.
        k: RRF constant (default 60, per Cormack et al. 2009).
        doc_key: Field name used as the unique document identifier (default "doc_id").

    Returns:
        RagResponse with merged, RRF-scored docs sorted descending. Each doc has
        its original fields plus an rrf_score float field.
    """
    if k <= 0:
        raise ValueError("k must be greater than 0")

    docs_a = results_a.docs
    docs_b = results_b.docs

    scores: dict[str, float] = {}
    doc_map: dict[str, RagDocument] = {}

    _accumulate_scores(docs_a, scores, doc_map, k, doc_key)
    _accumulate_scores(docs_b, scores, doc_map, k, doc_key)

    sorted_docs = [
        doc_map[identifier].model_copy(update={"rrf_score": score})
        for identifier, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]

    return RagResponse(num_found=len(sorted_docs), docs=sorted_docs)
