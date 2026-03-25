"""Tests for reciprocal rank fusion merging of Solr result sets."""

import pytest

from okp_mcp.rag.rrf import reciprocal_rank_fusion


def _solr_response(*docs: dict) -> dict:
    """Wrap docs in a Solr-shaped response dict."""
    return {"response": {"numFound": len(docs), "docs": list(docs)}}


def _doc(doc_id: str, title: str = "") -> dict:
    """Create a minimal Solr doc with doc_id and optional title."""
    return {"doc_id": doc_id, "title": title}


class TestReciprocalRankFusion:
    """Tests for reciprocal_rank_fusion()."""

    def test_overlapping_docs_score_higher_than_single_list(self):
        """A doc appearing in both lists gets a higher rrf_score than single-list docs."""
        shared = _doc("shared", "Shared Doc")
        results_a = _solr_response(shared, _doc("only_a", "Only A"))
        results_b = _solr_response(shared, _doc("only_b", "Only B"))

        merged = reciprocal_rank_fusion(results_a, results_b)

        docs = merged["response"]["docs"]
        scores = {d["doc_id"]: d["rrf_score"] for d in docs}

        assert scores["shared"] > scores["only_a"]
        assert scores["shared"] > scores["only_b"]
        assert scores["shared"] == pytest.approx(1 / 60 + 1 / 60)
        assert docs[0]["doc_id"] == "shared"

    def test_disjoint_docs_all_appear_in_output(self):
        """All docs from both lists appear in the output when there is no overlap."""
        results_a = _solr_response(_doc("a1"), _doc("a2"))
        results_b = _solr_response(_doc("b1"), _doc("b2"))

        merged = reciprocal_rank_fusion(results_a, results_b)

        doc_ids = {d["doc_id"] for d in merged["response"]["docs"]}
        assert doc_ids == {"a1", "a2", "b1", "b2"}
        assert merged["response"]["numFound"] == 4

    def test_one_empty_list_returns_populated_list_with_scores(self):
        """When one list is empty, output equals the populated list's docs with rrf_scores."""
        results_a = _solr_response(_doc("x1", "First"), _doc("x2", "Second"))
        results_b = _solr_response()

        merged = reciprocal_rank_fusion(results_a, results_b)

        docs = merged["response"]["docs"]
        assert len(docs) == 2
        assert docs[0]["doc_id"] == "x1"
        assert docs[0]["rrf_score"] == pytest.approx(1 / 60)
        assert docs[1]["rrf_score"] == pytest.approx(1 / 61)
        assert docs[0]["title"] == "First"

    def test_both_empty_returns_empty_response(self):
        """When both lists are empty, returns zero-result response."""
        merged = reciprocal_rank_fusion(_solr_response(), _solr_response())

        assert merged == {"response": {"numFound": 0, "docs": []}}

    def test_custom_k_changes_scores(self):
        """Custom k value produces different scores than the default k=60."""
        results_a = _solr_response(_doc("d1"))
        results_b = _solr_response()

        default_merged = reciprocal_rank_fusion(results_a, results_b, k=60)
        custom_merged = reciprocal_rank_fusion(results_a, results_b, k=10)

        default_score = default_merged["response"]["docs"][0]["rrf_score"]
        custom_score = custom_merged["response"]["docs"][0]["rrf_score"]

        assert default_score == pytest.approx(1 / 60)
        assert custom_score == pytest.approx(1 / 10)
        assert custom_score > default_score

    def test_custom_doc_key_uses_specified_field(self):
        """Using doc_key='parent_id' correctly identifies and merges docs."""
        doc_a = {"parent_id": "parent_1", "chunk": "text a"}
        doc_b = {"parent_id": "parent_1", "chunk": "text b"}
        results_a = _solr_response(doc_a)
        results_b = _solr_response(doc_b)

        merged = reciprocal_rank_fusion(results_a, results_b, doc_key="parent_id")

        docs = merged["response"]["docs"]
        assert len(docs) == 1
        assert docs[0]["parent_id"] == "parent_1"
        assert docs[0]["rrf_score"] == pytest.approx(1 / 60 + 1 / 60)
