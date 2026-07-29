import math
import unittest

from cognitive_memory.ranking import Bm25Scorer, bm25_idf


class Bm25Tests(unittest.TestCase):
    def test_idf_matches_lucene_formula(self):
        # N=3, df=1 -> ln(1 + (3-1+0.5)/(1+0.5)) = ln(1 + 2.5/1.5)
        self.assertAlmostEqual(bm25_idf(1, 3), math.log(1 + 2.5 / 1.5), places=9)
        # df==N -> ln(1 + 0.5/(N+0.5)), small positive, never negative
        self.assertGreater(bm25_idf(3, 3), 0.0)
        self.assertEqual(bm25_idf(0, 3), 0.0)
        self.assertEqual(bm25_idf(1, 0), 0.0)

    def test_score_matches_hand_computation(self):
        docs = [
            ["the", "cat", "sat"],
            ["the", "dog", "ran"],
            ["the", "cat", "ran", "fast"],
        ]
        s = Bm25Scorer(docs, k1=1.5, b=0.75)
        # hand-compute score of query "cat" against doc 0
        N = 3
        avgdl = (3 + 3 + 4) / 3
        df_cat = 2  # docs 0 and 2
        idf = math.log(1 + (N - df_cat + 0.5) / (df_cat + 0.5))
        dl0 = 3
        freq = 1
        denom = freq + 1.5 * (1 - 0.75 + 0.75 * dl0 / avgdl)
        expected = idf * (freq * (1.5 + 1.0)) / denom
        self.assertAlmostEqual(s.score(["cat"], 0), expected, places=9)

    def test_ubiquitous_term_downweighted(self):
        docs = [["the", "cat"], ["the", "dog"], ["the", "bird"]]
        s = Bm25Scorer(docs)
        # "the" appears in every doc -> near-zero contribution
        self.assertLess(s.score(["the"], 0), s.score(["cat"], 0))

    def test_length_normalization_prefers_concise_match(self):
        docs = [
            ["cat"],
            ["cat"] + ["filler"] * 20,
        ]
        s = Bm25Scorer(docs)
        self.assertGreater(s.score(["cat"], 0), s.score(["cat"], 1))

    def test_deterministic_and_order_independent(self):
        docs = [["a", "b"], ["b", "c"], ["c", "d", "a"]]
        s1 = Bm25Scorer(docs)
        s2 = Bm25Scorer(list(reversed(docs)))
        # score of query "a" on the doc ["a","b"] is identical regardless of
        # corpus ordering (same doc is index 0 in s1, index 2 in s2)
        self.assertAlmostEqual(s1.score(["a"], 0), s2.score(["a"], 2), places=12)

    def test_empty_query_or_corpus(self):
        self.assertEqual(Bm25Scorer([]).score(["x"], 0), 0.0)
        s = Bm25Scorer([["a", "b"]])
        self.assertEqual(s.score([], 0), 0.0)
        self.assertEqual(s.score(["a"], 5), 0.0)

    def test_normalized_score_in_unit_interval(self):
        docs = [["cat", "sat"], ["dog", "ran"]]
        s = Bm25Scorer(docs)
        n = s.normalized_score(["cat"], 0)
        self.assertGreater(n, 0.0)
        self.assertLess(n, 1.0)
        self.assertEqual(s.normalized_score(["zzz"], 0), 0.0)

    def test_repeated_query_term_counted_once(self):
        docs = [["cat", "cat", "dog"], ["dog"]]
        s = Bm25Scorer(docs)
        self.assertEqual(s.score(["cat", "cat"], 0), s.score(["cat"], 0))


if __name__ == "__main__":
    unittest.main()
