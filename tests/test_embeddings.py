"""Tests for the key-gated dense-retrieval support (cache, embedder, RRF)."""

import tempfile
import unittest
from pathlib import Path

from cognitive_memory.embeddings import CachedEmbedder, DiskVectorCache, cosine, rrf_fuse


class MockProvider:
    model = "mock-embed"

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        # deterministic 4-dim vectors from character statistics
        out = []
        for text in texts:
            t = text.lower()
            out.append([1.0 + t.count("a"), 1.0 + t.count("e"), 1.0 + t.count("i"), float(len(t) % 7)])
        return out


class CachedEmbedderTests(unittest.TestCase):
    def test_cache_prevents_repeat_provider_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskVectorCache(str(Path(tmp) / "vec.jsonl"))
            provider = MockProvider()
            embedder = CachedEmbedder(provider, cache)
            first = embedder.embed(["alpha", "beta"])
            second = embedder.embed(["beta", "alpha", "gamma"])
            self.assertEqual(first[0], second[1])  # same text -> same vector
            # only 'gamma' hit the provider on the second call
            self.assertEqual(provider.calls[1], ["gamma"])

    def test_cache_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "vec.jsonl")
            embedder = CachedEmbedder(MockProvider(), DiskVectorCache(path))
            vec = embedder.embed(["persist me"])[0]
            fresh_provider = MockProvider()
            reopened = CachedEmbedder(fresh_provider, DiskVectorCache(path))
            self.assertEqual(reopened.embed(["persist me"])[0], vec)
            self.assertEqual(fresh_provider.calls, [])  # served from disk


class ScoringTests(unittest.TestCase):
    def test_cosine_bounds(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine([0, 0], [1, 1]), 0.0)  # zero vector safe

    def test_rrf_prefers_agreement(self):
        fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
        ids = [doc for doc, _ in fused]
        # a and b (present near top of both) beat c and d (single ranking each)
        self.assertEqual(set(ids[:2]), {"a", "b"})
        self.assertTrue(all(score > 0 for _, score in fused))

    def test_rrf_deterministic_tiebreak(self):
        fused = rrf_fuse([["x"], ["y"]])
        self.assertEqual([doc for doc, _ in fused], ["x", "y"])  # equal score -> lexicographic


if __name__ == "__main__":
    unittest.main()
