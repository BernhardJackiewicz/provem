"""Pure-stdlib Okapi BM25 for conversational-memory retrieval.

Rationale (from the 2024-2026 literature review): on LoCoMo-style conversational
memory, lexical BM25 is competitive with, and often beats, mean-pooled dense
retrieval (SeCom / ICLR 2025; arXiv 2606.04194), and it is trivially portable
with no embedding model or API key. This module replaces the token-overlap ratio
in the hybrid retrieval planner's content component with a proper length- and
IDF-normalized BM25 score.

No third-party dependencies. Scores are made comparable to the other [0,1]
retrieval features via a saturating normalization ``s/(s+k)``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence


def bm25_idf(df: int, corpus_size: int) -> float:
    """Lucene-style BM25 IDF: ln(1 + (N - df + 0.5)/(df + 0.5)).

    Always non-negative (unlike the classic Robertson-Sparck-Jones form), so a
    term present in every document contributes ~0 rather than a negative weight.
    """
    if corpus_size <= 0 or df <= 0:
        return 0.0
    return math.log(1.0 + (corpus_size - df + 0.5) / (df + 0.5))


class Bm25Scorer:
    """Okapi BM25 over a fixed document corpus of pre-tokenized term lists.

    Parameters ``k1`` (term-frequency saturation) and ``b`` (length
    normalization) use the standard defaults. The corpus is the set of candidate
    memories for one retrieval call, so IDF is computed within the request scope
    (deterministic, no cross-sample leakage).
    """

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.documents: List[List[str]] = [list(doc) for doc in documents]
        self.corpus_size = len(self.documents)
        self.doc_len = [len(doc) for doc in self.documents]
        self.avgdl = (sum(self.doc_len) / self.corpus_size) if self.corpus_size else 0.0
        self._tf: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for doc in self.documents:
            counts: Dict[str, int] = {}
            for term in doc:
                counts[term] = counts.get(term, 0) + 1
            self._tf.append(counts)
            for term in counts:
                df[term] = df.get(term, 0) + 1
        self._idf: Dict[str, float] = {t: bm25_idf(d, self.corpus_size) for t, d in df.items()}

    def score(self, query_terms: Sequence[str], doc_index: int) -> float:
        if not (0 <= doc_index < self.corpus_size) or self.avgdl == 0:
            return 0.0
        tf = self._tf[doc_index]
        dl = self.doc_len[doc_index]
        length_norm = self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
        total = 0.0
        seen = set()
        for term in query_terms:
            if term in seen:
                continue
            seen.add(term)
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            idf = self._idf.get(term, 0.0)
            total += idf * (freq * (self.k1 + 1.0)) / (freq + length_norm)
        return total

    def normalized_score(self, query_terms: Sequence[str], doc_index: int, k: float = 3.0) -> float:
        """Saturating map of the raw BM25 score into [0, 1) for feature fusion."""
        raw = self.score(query_terms, doc_index)
        if raw <= 0.0:
            return 0.0
        return raw / (raw + k)
