"""Tests for reciprocal rank fusion merging of Solr result sets."""

import pytest

from okp_mcp.rag.models import RagDocument, RagResponse
from okp_mcp.rag.rrf import reciprocal_rank_fusion


def _solr_response(*docs: RagDocument) -> RagResponse:
    """Wrap docs in a RagResponse."""
    return RagResponse(num_found=len(docs), docs=list(docs))


def _doc(doc_id: str, title: str = "") -> RagDocument:
    """Create a minimal RagDocument with doc_id and optional title."""
    return RagDocument(doc_id=doc_id, title=title)


class TestReciprocalRankFusion:
    """Tests for reciprocal_rank_fusion()."""

    def test_overlapping_docs_score_higher_than_single_list(self):
        """A doc appearing in both lists gets a higher rrf_score than single-list docs."""
        shared = _doc("shared", "Shared Doc")
        results_a = _solr_response(shared, _doc("only_a", "Only A"))
        results_b = _solr_response(shared, _doc("only_b", "Only B"))

        merged = reciprocal_rank_fusion(results_a, results_b)

        docs = merged.docs
        scores = {d.doc_id: d.rrf_score for d in docs}
        shared_score = scores["shared"]
        only_a_score = scores["only_a"]
        only_b_score = scores["only_b"]

        assert shared_score is not None
        assert only_a_score is not None
        assert only_b_score is not None

        assert shared_score > only_a_score
        assert shared_score > only_b_score
        assert shared_score == pytest.approx(1 / 60 + 1 / 60)
        assert docs[0].doc_id == "shared"

    def test_disjoint_docs_all_appear_in_output(self):
        """All docs from both lists appear in the output when there is no overlap."""
        results_a = _solr_response(_doc("a1"), _doc("a2"))
        results_b = _solr_response(_doc("b1"), _doc("b2"))

        merged = reciprocal_rank_fusion(results_a, results_b)

        doc_ids = {d.doc_id for d in merged.docs}
        assert doc_ids == {"a1", "a2", "b1", "b2"}
        assert merged.num_found == 4

    def test_one_empty_list_returns_populated_list_with_scores(self):
        """When one list is empty, output equals the populated list's docs with rrf_scores."""
        results_a = _solr_response(_doc("x1", "First"), _doc("x2", "Second"))
        results_b = _solr_response()

        merged = reciprocal_rank_fusion(results_a, results_b)

        docs = merged.docs
        assert len(docs) == 2
        assert docs[0].doc_id == "x1"
        assert docs[0].rrf_score == pytest.approx(1 / 60)
        assert docs[1].rrf_score == pytest.approx(1 / 61)
        assert docs[0].title == "First"

    def test_both_empty_returns_empty_response(self):
        """When both lists are empty, returns zero-result response."""
        merged = reciprocal_rank_fusion(_solr_response(), _solr_response())

        assert merged == RagResponse(num_found=0, docs=[])

    def test_custom_k_changes_scores(self):
        """Custom k value produces different scores than the default k=60."""
        results_a = _solr_response(_doc("d1"))
        results_b = _solr_response()

        default_merged = reciprocal_rank_fusion(results_a, results_b, k=60)
        custom_merged = reciprocal_rank_fusion(results_a, results_b, k=10)

        default_score = default_merged.docs[0].rrf_score
        custom_score = custom_merged.docs[0].rrf_score

        assert default_score is not None
        assert custom_score is not None

        assert default_score == pytest.approx(1 / 60)
        assert custom_score == pytest.approx(1 / 10)
        assert custom_score > default_score

    @pytest.mark.parametrize("k", [0, -1, -60])
    def test_invalid_k_raises_value_error(self, k):
        """reciprocal_rank_fusion raises ValueError when k <= 0."""
        with pytest.raises(ValueError, match="k must be greater than 0"):
            reciprocal_rank_fusion(_solr_response(), _solr_response(), k=k)

    def test_custom_doc_key_uses_specified_field(self):
        """Using doc_key='parent_id' correctly identifies and merges docs."""
        doc_a = RagDocument(parent_id="parent_1", chunk="text a")
        doc_b = RagDocument(parent_id="parent_1", chunk="text b")
        results_a = _solr_response(doc_a)
        results_b = _solr_response(doc_b)

        merged = reciprocal_rank_fusion(results_a, results_b, doc_key="parent_id")

        docs = merged.docs
        assert len(docs) == 1
        assert docs[0].parent_id == "parent_1"
        assert docs[0].rrf_score == pytest.approx(1 / 60 + 1 / 60)

    def test_zero_inputs_returns_empty(self):
        """reciprocal_rank_fusion() with no args returns empty RagResponse."""
        merged = reciprocal_rank_fusion()
        assert merged == RagResponse(num_found=0, docs=[])

    def test_single_input_returns_unchanged(self):
        """reciprocal_rank_fusion(response_a) returns response_a unchanged (no rrf_score)."""
        response_a = _solr_response(_doc("x1"), _doc("x2"))
        result = reciprocal_rank_fusion(response_a)
        assert result is response_a

    def test_three_way_fusion_scores(self):
        """Doc in all 3 lists scores highest; doc in 2 scores middle; doc in 1 scores lowest."""
        shared = _doc("shared")
        two_list = _doc("two_list")
        one_list = _doc("one_list")

        results_a = _solr_response(shared, two_list, one_list)
        results_b = _solr_response(shared, two_list)
        results_c = _solr_response(shared)

        merged = reciprocal_rank_fusion(results_a, results_b, results_c)
        scores = {d.doc_id: d.rrf_score for d in merged.docs}

        assert scores["shared"] is not None
        assert scores["two_list"] is not None
        assert scores["one_list"] is not None
        assert scores["shared"] > scores["two_list"]
        assert scores["two_list"] > scores["one_list"]

    def test_three_way_disjoint(self):
        """Three result sets with no overlap all appear in output."""
        results_a = _solr_response(_doc("a1"), _doc("a2"))
        results_b = _solr_response(_doc("b1"), _doc("b2"))
        results_c = _solr_response(_doc("c1"), _doc("c2"))

        merged = reciprocal_rank_fusion(results_a, results_b, results_c)

        doc_ids = {d.doc_id for d in merged.docs}
        assert doc_ids == {"a1", "a2", "b1", "b2", "c1", "c2"}
        assert merged.num_found == 6
