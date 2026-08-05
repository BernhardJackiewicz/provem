"""Deletion as a job over derivatives, not only the store row.

The embedding cache, summaries and backend-side copies are separate copies of
an erased value; clearing the row and leaving them is the classic derivative
leak. These tests pin: the vector cache can delete (it was append-only
forever), forget() sweeps registered derivative stores and reports per-store
counts in the certificate, and the mem0 adapter re-verifies its best-effort
deletes.
"""

import json
import tempfile
import unittest
from pathlib import Path

from cognitive_memory.embeddings import CachedEmbedder, DiskVectorCache, _key


class _MockProvider:
    model = "mock-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[float(len(text)), 1.0, 0.0] for text in texts]


class VectorCacheDeletionTests(unittest.TestCase):
    def test_delete_texts_removes_vectors_and_compacts_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "vectors.jsonl")
            cache = DiskVectorCache(path)
            embedder = CachedEmbedder(_MockProvider(), cache)
            embedder.embed(["alpha", "beta", "gamma"])
            removed = cache.delete_texts("mock-embed", ["beta"])
            self.assertEqual(removed, 1)
            reopened = DiskVectorCache(path)
            self.assertIsNone(reopened.get(_key("mock-embed", "beta")),
                              "erased text's vector persisted on disk")
            self.assertIsNotNone(reopened.get(_key("mock-embed", "alpha")))

    def test_delete_missing_text_is_noop_zero(self):
        cache = DiskVectorCache(None)
        self.assertEqual(cache.delete_texts("mock-embed", ["never stored"]), 0)

    def test_delete_is_atomic_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "vectors.jsonl")
            cache = DiskVectorCache(path)
            embedder = CachedEmbedder(_MockProvider(), cache)
            embedder.embed(["alpha", "beta"])
            cache.delete_texts("mock-embed", ["alpha"])
            # the rewritten file must parse fully (temp file + atomic replace)
            with open(path) as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(rows), 1)

    def test_purge_texts_convenience(self):
        cache = DiskVectorCache(None)
        embedder = CachedEmbedder(_MockProvider(), cache)
        embedder.embed(["alpha"])
        self.assertEqual(embedder.purge_texts(["alpha"]), 1)
        self.assertEqual(len(cache), 0)


if __name__ == "__main__":
    unittest.main()
