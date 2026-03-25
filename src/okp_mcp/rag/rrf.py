"""Reciprocal rank fusion for merging multiple Solr result sets."""

import logging

logger = logging.getLogger(__name__)


def _accumulate_scores(
    docs: list[dict], scores: dict[str, float], doc_map: dict[str, dict], k: int, doc_key: str
) -> None:
    """Accumulate RRF scores and collect doc data from a single result list.

    Args:
        docs: List of Solr document dicts.
        scores: Mutable score accumulator keyed by doc identifier.
        doc_map: Mutable dict collecting the first-seen version of each doc.
        k: RRF constant.
        doc_key: Field name used as the unique document identifier.
    """
    for rank, doc in enumerate(docs):
        identifier = doc.get(doc_key)
        if not identifier:
            logger.warning("Skipping doc at rank %d missing key %r", rank, doc_key)
            continue
        scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + rank)
        if identifier not in doc_map:
            doc_map[identifier] = doc


def reciprocal_rank_fusion(
    results_a: dict,
    results_b: dict,
    *,
    k: int = 60,
    doc_key: str = "doc_id",
) -> dict:
    """Merge two Solr result sets using reciprocal rank fusion (RRF).

    Combines two result lists by computing RRF scores: for each unique document,
    sums 1/(k + rank) across all lists where it appears (rank is 0-indexed position).
    Documents appearing in both lists score higher than single-list documents.

    Args:
        results_a: First Solr response dict with {"response": {"docs": [...]}}.
        results_b: Second Solr response dict with {"response": {"docs": [...]}}.
        k: RRF constant (default 60, per Cormack et al. 2009).
        doc_key: Field name used as the unique document identifier (default "doc_id").

    Returns:
        New Solr-shaped response dict with merged, RRF-scored docs sorted descending.
        Each doc has its original fields plus an "rrf_score" float field.
    """
    docs_a = results_a.get("response", {}).get("docs", [])
    docs_b = results_b.get("response", {}).get("docs", [])

    scores: dict[str, float] = {}
    doc_map: dict[str, dict] = {}

    _accumulate_scores(docs_a, scores, doc_map, k, doc_key)
    _accumulate_scores(docs_b, scores, doc_map, k, doc_key)

    sorted_docs = [
        {**doc_map[identifier], "rrf_score": score}
        for identifier, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]

    return {"response": {"numFound": len(sorted_docs), "docs": sorted_docs}}
