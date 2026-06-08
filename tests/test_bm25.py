"""Characterization tests for the pure-Python BM25Plus implementation."""

import math

import pytest

from okp_mcp.bm25 import BM25Plus

# (corpus, query, expected_scores) triples captured from rank_bm25.BM25Plus.
GOLDEN_CASES = [
    pytest.param(
        [
            ["the", "quick", "brown", "fox"],
            ["the", "lazy", "dog"],
            ["quick", "fox", "jumps", "over", "lazy", "dog"],
        ],
        ["quick", "fox"],
        [2.822296488176351, 1.3862943611198906, 2.5680534886319286],
        id="basic",
    ),
    pytest.param(
        [["alpha", "beta"], ["gamma"]],
        ["zeta"],
        [0.0, 0.0],
        id="term_absent_all",
    ),
    pytest.param(
        [["a", "a", "a", "b"], ["a", "b", "b"], ["c"]],
        ["a", "b"],
        [2.9790135061707095, 2.9944901712617655, 1.3862943611198906],
        id="repeated_terms",
    ),
    pytest.param(
        [["solr", "query", "timeout"]],
        ["timeout", "solr"],
        [2.772588722239781],
        id="single_doc",
    ),
    pytest.param(
        [["x", "y"], ["z"]],
        [],
        [0.0, 0.0],
        id="empty_query",
    ),
    pytest.param(
        [
            ["red", "hat", "enterprise", "linux"],
            ["fedora", "linux"],
            ["debian"],
        ],
        ["linux", "windows"],
        [1.2176909928755797, 1.4339151597843143, 0.6931471805599453],
        id="mixed_known_unknown",
    ),
    pytest.param(
        [["a"], []],
        ["a"],
        [1.8562759360254268, 1.0986122886681098],
        id="mixed_one_empty_doc_known_term",
    ),
    pytest.param(
        [["a"], []],
        ["b"],
        [0.0, 0.0],
        id="mixed_one_empty_doc_unknown_term",
    ),
]


@pytest.mark.parametrize(("corpus", "query", "expected"), GOLDEN_CASES)
def test_get_scores_matches_rank_bm25(corpus, query, expected):
    """get_scores reproduces rank_bm25.BM25Plus output exactly."""
    bm25 = BM25Plus(corpus)
    scores = bm25.get_scores(query)
    assert len(scores) == len(expected)
    for got, want in zip(scores, expected, strict=True):
        assert math.isclose(got, want, rel_tol=1e-12, abs_tol=1e-12)


def test_get_scores_returns_indexable_floats():
    """Scores are positionally indexable and float-coercible (solr.py contract)."""
    bm25 = BM25Plus([["a", "b"], ["b", "c"]])
    scores = bm25.get_scores(["b"])
    assert float(scores[0]) >= 0.0
    assert float(scores[1]) >= 0.0


def test_default_parameters_match_library():
    """Default k1/b/delta match rank_bm25.BM25Plus defaults."""
    bm25 = BM25Plus([["a"]])
    assert bm25.k1 == 1.5
    assert bm25.b == 0.75
    assert bm25.delta == 1


@pytest.mark.parametrize(
    ("corpus", "query"),
    [
        pytest.param([[]], ["a"], id="single_empty_doc"),
        pytest.param([[], []], ["a"], id="all_empty_docs"),
        pytest.param([], ["a"], id="empty_corpus"),
    ],
)
def test_degenerate_empty_corpora_do_not_crash(corpus, query):
    """All-empty / empty corpora return finite zeros instead of NaN or raising.

    rank_bm25 returns NaN here (numpy divide-by-zero on avgdl=0). This impl
    deliberately returns 0.0 because empty paragraphs are filtered upstream in
    okp_mcp.solr, so the case is unreachable and 0.0 is the saner result.
    """
    scores = BM25Plus(corpus).get_scores(query)
    assert len(scores) == len(corpus)
    assert all(s == 0.0 for s in scores)
