"""Semantic erasure: opt-in paraphrase matching on top of token tombstones.

Token-subset matching cannot connect "wants kids" to "planning a family".
Prerequisite: the raw erased term string must be retained where enforcement
reads (it previously existed only inside the audit certificate), so a
semantic check has something to compare against. The semantic check itself is
opt-in (threshold + injected embedder), deterministic in tests via a fake
embedder, and fails open with an audit entry: token erasure stays the
deterministic baseline, a flaky provider must not take recall down.
"""

import unittest

from cognitive_memory.reliability import GovernedMemory, Scope


class ErasedTermRetentionTests(unittest.TestCase):
    def test_forget_retains_raw_term_text(self):
        mem = GovernedMemory()
        mem.forget("wants kids", Scope(tenant="t"))
        self.assertEqual(mem.erased_term_texts.get("t"), ["wants kids"])

    def test_raw_terms_rehydrate_from_sqlite_tombstones(self):
        import tempfile
        from pathlib import Path

        from cognitive_memory.adapters.sqlite_backend import SqliteBackend

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            mem = GovernedMemory(backend=SqliteBackend(db))
            mem.forget("wants kids", Scope(tenant="t"))
            mem2 = GovernedMemory(backend=SqliteBackend(db))  # restart
            self.assertIn("wants kids", mem2.erased_term_texts.get("t", []))

    def test_policy_store_retains_raw_term_and_roundtrips(self):
        from cognitive_memory.policy import PolicyStore

        policy = PolicyStore()
        policy.apply_deletion_term("wants kids")
        self.assertIn("wants kids", policy.do_not_use_term_texts)
        restored = PolicyStore.from_dict(policy.to_dict())
        self.assertIn("wants kids", restored.do_not_use_term_texts)
        legacy = policy.to_dict()
        legacy.pop("do_not_use_term_texts", None)
        self.assertEqual(PolicyStore.from_dict(legacy).do_not_use_term_texts, [])


class FakeSemanticEmbedder:
    """Deterministic test embedder: texts in the same configured group share a
    unit vector, everything else gets a stable distinct direction (no
    network, no keys, no per-process hash salt)."""

    def __init__(self, groups=()):
        self._group_of = {}
        for index, group in enumerate(groups):
            for text in group:
                self._group_of[text.lower()] = index

    def embed(self, texts):
        import hashlib

        vectors = []
        for text in texts:
            vector = [0.0] * 16
            index = self._group_of.get(text.lower())
            if index is not None:
                vector[index] = 1.0
            else:
                digest = int(hashlib.sha256(text.lower().encode("utf-8")).hexdigest(), 16)
                vector[8 + (digest % 8)] = 1.0
            vectors.append(vector)
        return vectors


class _RaisingEmbedder:
    def embed(self, texts):
        raise RuntimeError("provider down")


PARAPHRASE = "alice is planning a family"


class SemanticErasureTests(unittest.TestCase):
    def _mem(self, embedder=None, threshold=0.8):
        policy = {"name": "s"}
        if threshold is not None:
            policy["semantic_erasure_threshold"] = threshold
        mem = GovernedMemory(policy=policy, embedder=embedder)
        mem.remember(PARAPHRASE, subject="alice", relation="note", object="",
                     tenant="t", entity="alice")
        mem.forget("wants kids", Scope(tenant="t"))
        return mem

    def test_off_by_default_paraphrase_leaks(self):
        # Documents the gap AND guards the headline: without opt-in, token
        # matching alone runs and the paraphrase is served.
        mem = GovernedMemory()
        mem.remember(PARAPHRASE, subject="alice", relation="note", object="",
                     tenant="t", entity="alice")
        mem.forget("wants kids", Scope(tenant="t"))
        result = mem.recall_value("alice planning family", tenant="t", entity="alice")
        self.assertFalse(result.abstained)

    def test_paraphrase_refused_with_reason_erased_semantic(self):
        embedder = FakeSemanticEmbedder(groups=[("wants kids", PARAPHRASE)])
        mem = self._mem(embedder=embedder)
        result = mem.recall_value("alice planning family", tenant="t", entity="alice")
        self.assertTrue(result.abstained)
        self.assertIn("erased_semantic", {reason for _, reason in result.excluded})

    def test_below_threshold_not_blocked(self):
        embedder = FakeSemanticEmbedder(groups=[])  # everything orthogonal
        mem = self._mem(embedder=embedder)
        result = mem.recall_value("alice planning family", tenant="t", entity="alice")
        self.assertFalse(result.abstained)

    def test_threshold_without_embedder_is_token_only(self):
        mem = self._mem(embedder=None)
        result = mem.recall_value("alice planning family", tenant="t", entity="alice")
        self.assertFalse(result.abstained)

    def test_embedder_failure_fails_open_with_audit(self):
        mem = self._mem(embedder=_RaisingEmbedder())
        result = mem.recall_value("alice planning family", tenant="t", entity="alice")
        self.assertFalse(result.abstained, "a flaky provider must not take recall down")
        self.assertTrue(mem.audit.filter("semantic_erasure_error"),
                        "the degradation must be visible in the audit trail")

    def test_policy_threshold_serialization_and_bounds(self):
        from cognitive_memory.compliance import CompliancePolicy, ComplianceConfigError

        policy = CompliancePolicy(name="s", semantic_erasure_threshold=0.8)
        restored = CompliancePolicy.from_json(policy.to_json())
        self.assertEqual(restored, policy)
        with self.assertRaises(ComplianceConfigError):
            CompliancePolicy(semantic_erasure_threshold=1.5)


class PrototypeSemanticErasureTests(unittest.TestCase):
    def _policy_with_term(self):
        from cognitive_memory.policy import PolicyStore

        policy = PolicyStore()
        policy.apply_deletion_term("wants kids")
        return policy

    def test_matches_do_not_use_term_semantic_opt_in(self):
        policy = self._policy_with_term()

        def matcher(text, term):
            related = {"wants kids", PARAPHRASE.lower()}
            return 1.0 if text.lower() in related and term.lower() in related else 0.0

        policy.set_semantic_matcher(matcher, threshold=0.8)
        # covers facts, events AND reflections: every exclusion path funnels
        # through matches_do_not_use_term
        self.assertTrue(policy.matches_do_not_use_term(PARAPHRASE))
        self.assertFalse(policy.matches_do_not_use_term("bob prefers tea"))

    def test_off_by_default(self):
        policy = self._policy_with_term()
        self.assertFalse(policy.matches_do_not_use_term(PARAPHRASE))


if __name__ == "__main__":
    unittest.main()
