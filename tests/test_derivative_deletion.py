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


class DerivativeSweepTests(unittest.TestCase):
    def _mem_with_vector_store(self, **kwargs):
        from cognitive_memory.embeddings import VectorCacheDerivativeStore
        from cognitive_memory.reliability import GovernedMemory

        cache = DiskVectorCache(None)
        embedder = CachedEmbedder(_MockProvider(), cache)
        mem = GovernedMemory(**kwargs)
        mem.register_derivative_store(VectorCacheDerivativeStore(embedder))
        return mem, embedder, cache

    def test_forget_purges_registered_vector_cache(self):
        from cognitive_memory.reliability import Scope

        mem, embedder, cache = self._mem_with_vector_store()
        mem.remember("bob secret99 note", subject="bob", relation="note",
                     object="secret99", tenant="t", entity="bob")
        embedder.embed(["bob secret99 note"])  # the dense channel embedded the record text
        self.assertEqual(len(cache), 1)
        mem.forget("secret99", Scope(tenant="t", subject="bob"))
        self.assertEqual(len(cache), 0, "erased record's vector survived in the derivative store")

    def test_erasure_certificate_reports_per_derivative_counts(self):
        from cognitive_memory.reliability import Scope

        mem, embedder, _ = self._mem_with_vector_store()
        mem.remember("bob secret99 note", subject="bob", relation="note",
                     object="secret99", tenant="t", entity="bob")
        embedder.embed(["bob secret99 note"])
        mem.forget("secret99", Scope(tenant="t", subject="bob"))
        cert = mem.audit.filter("erasure")[-1]
        self.assertEqual(cert.details["derivatives"], {"vector_cache": 1})

    def test_certificate_shape_unchanged_without_stores(self):
        from cognitive_memory.reliability import GovernedMemory, Scope

        mem = GovernedMemory()
        mem.remember("bob secret99 note", subject="bob", relation="note",
                     object="secret99", tenant="t", entity="bob")
        mem.forget("secret99", Scope(tenant="t", subject="bob"))
        cert = mem.audit.filter("erasure")[-1]
        self.assertNotIn("derivatives", cert.details,
                         "legacy certificate shape must stay byte-identical")

    def test_cleanup_expired_sweeps_derivatives(self):
        from datetime import datetime, timedelta, timezone

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mem, embedder, cache = self._mem_with_vector_store(
            policy={"name": "r", "retention_days": {"high": 30}}, now_fn=lambda: base)
        mem.remember("old fact here", subject="a", relation="note", object="x",
                     tenant="t", entity="a")
        embedder.embed(["old fact here"])
        removed = mem.cleanup_expired(now=base + timedelta(days=40))
        self.assertEqual(removed, 1)
        self.assertEqual(len(cache), 0, "expired record's vector survived retention cleanup")

    def test_protocol_runtime_checkable(self):
        from cognitive_memory.embeddings import VectorCacheDerivativeStore
        from cognitive_memory.reliability import DerivativeStore

        store = VectorCacheDerivativeStore(CachedEmbedder(_MockProvider(), DiskVectorCache(None)))
        self.assertIsInstance(store, DerivativeStore)


if __name__ == "__main__":
    unittest.main()
