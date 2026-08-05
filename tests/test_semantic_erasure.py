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


if __name__ == "__main__":
    unittest.main()
