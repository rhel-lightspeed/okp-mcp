"""Pure-Python BM25+ scorer (drop-in for rank_bm25.BM25Plus, no numpy)."""

import math

from collections import Counter
from collections.abc import Sequence


class BM25Plus:
    """BM25+ ranking over a tokenized corpus, matching rank_bm25.BM25Plus.

    Covers the subset of behavior okp-mcp relies on: construct with a tokenized
    corpus, then call :meth:`get_scores` with a list of query terms. Implemented
    without numpy so the runtime container needs no C/C++ extension libraries.

    The scoring formula mirrors rank_bm25 v0.2.2's ``BM25Plus`` exactly::

        idf(q)   = log((N + 1) / df(q))
        score(d) = sum over q in query of
                   idf(q) * (delta + (f(q,d) * (k1 + 1))
                                     / (k1 * (1 - b + b * |d| / avgdl) + f(q,d)))

    where N is the corpus size, df(q) the document frequency of term q, f(q,d)
    the term frequency in document d, |d| the document length, and avgdl the
    mean document length. Unknown query terms contribute 0.

    Divergence from rank_bm25: a corpus of only empty documents yields scores of
    0.0 here, where rank_bm25 returns NaN (numpy divide-by-zero on avgdl=0).
    Callers (okp_mcp.solr) filter empty paragraphs before scoring, so this case
    is unreachable in practice; 0.0 is the more sensible result regardless.
    """

    def __init__(
        self,
        corpus: Sequence[Sequence[str]],
        k1: float = 1.5,
        b: float = 0.75,
        delta: float = 1,
    ) -> None:
        """Index ``corpus`` (a sequence of token lists) for scoring.

        ``k1``, ``b``, and ``delta`` default to the same values as
        rank_bm25.BM25Plus.
        """
        self.k1 = k1
        self.b = b
        self.delta = delta

        self.corpus_size = 0
        self.doc_len: list[int] = []
        self.doc_freqs: list[dict[str, int]] = []
        nd: dict[str, int] = {}  # term -> number of documents containing it
        num_tokens = 0

        for document in corpus:
            self.doc_len.append(len(document))
            num_tokens += len(document)
            frequencies = Counter(document)
            self.doc_freqs.append(dict(frequencies))
            for term in frequencies:
                nd[term] = nd.get(term, 0) + 1
            self.corpus_size += 1

        self.avgdl = num_tokens / self.corpus_size if self.corpus_size else 0.0
        self.idf = {term: math.log((self.corpus_size + 1) / freq) for term, freq in nd.items()}

    def get_scores(self, query: Sequence[str]) -> list[float]:
        """Return one BM25+ score per document, indexed by corpus position."""
        scores = [0.0] * self.corpus_size
        for q in query:
            idf = self.idf.get(q, 0)
            if not idf:
                continue
            for idx in range(self.corpus_size):
                q_freq = self.doc_freqs[idx].get(q, 0)
                denom = self.k1 * (1 - self.b + self.b * self.doc_len[idx] / self.avgdl) + q_freq
                scores[idx] += idf * (self.delta + (q_freq * (self.k1 + 1)) / denom)
        return scores
