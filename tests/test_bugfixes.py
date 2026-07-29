"""Regression tests for the 11 verified bug-hunt findings (one per reproduced case)."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return resp["result"]["structuredContent"]


class C1CrossTenantCleanupTests(unittest.TestCase):
    def test_cleanup_of_one_tenant_does_not_delete_another(self):
        from cognitive_memory.mcp_server import GovernedMemoryService, MCPServer, ServerConfig

        with tempfile.TemporaryDirectory() as tmp:
            cfg = ServerConfig.from_dict({
                "backend": "sqlite",
                "sqlite_path": str(Path(tmp) / "m.db"),
                "tenant_profiles": {"t1": {"name": "aggressive", "retention_days": {"high": 0}}, "t2": "default"},
            })
            server = MCPServer(GovernedMemoryService(cfg))
            _call(server, "remember", {"text": "bob fact here", "subject": "bob", "relation": "note",
                                       "object": "keepme", "tenant": "t2", "entity": "bob"})
            # t1 runs cleanup with a 0-day retention; it must NOT touch t2's record
            out = _call(server, "cleanup", {"tenant": "t1"})
            self.assertEqual(out["removed"], 0)
            rec = _call(server, "recall", {"query": "bob fact", "tenant": "t2", "entity": "bob"})
            self.assertFalse(rec["abstained"], "t2's record was destroyed by t1's cleanup")
            self.assertEqual(rec["answer"], "keepme")


class C2SqliteCounterTests(unittest.TestCase):
    def test_no_overwrite_after_delete_and_reopen(self):
        from cognitive_memory.adapters.sqlite_backend import SqliteBackend
        from cognitive_memory.reliability import MemoryRecord, Scope

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "m.db")
            b = SqliteBackend(db)
            b.write(MemoryRecord("a", "r", "1", Scope(tenant="t"), text="first"))
            b.write(MemoryRecord("b", "r", "2", Scope(tenant="t"), text="gone"))  # id s2
            b.delete_ids(["s1"])
            b.close()
            b2 = SqliteBackend(db)  # reopen; counter must resume past s2
            b2.write(MemoryRecord("c", "r", "3", Scope(tenant="t"), text="new"))
            texts = sorted(r.text for r in b2.all_records())
            self.assertEqual(texts, ["gone", "new"])  # survivor kept, new added, nothing overwritten


class C2FloatCoercionTests(unittest.TestCase):
    def test_quoted_number_profile_works(self):
        from cognitive_memory.compliance import CompliancePolicy
        from cognitive_memory.reliability import GovernedMemory

        p = CompliancePolicy.from_dict({"name": "x", "min_store_trust": "0.5", "relevance_floor": "0.4"})
        self.assertIsInstance(p.min_store_trust, float)
        mem = GovernedMemory(policy=p)
        mem.remember("alice likes coffee", subject="alice", relation="likes",
                     object="coffee", tenant="t", entity="alice", source="user", trust=0.9)
        # no TypeError on write/recall
        self.assertFalse(mem.recall_value("alice likes", tenant="t", entity="alice").abstained)


class C2AuditLoadTests(unittest.TestCase):
    def test_truncated_trailing_line_tolerated(self):
        from cognitive_memory.audit import AuditLog

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "a.jsonl")
            log = AuditLog(clock=lambda: "t", persist_path=path)
            log.record("quarantine", subject="x")
            log.record("erasure", term="s")
            # append a truncated/corrupt trailing line (crash-mid-append artifact)
            with open(path, "a", encoding="utf-8") as h:
                h.write('{"seq": 2, "action": "restr')
            reloaded = AuditLog(clock=lambda: "t", persist_path=path)
            self.assertEqual(len(reloaded), 2)  # valid prefix loaded, corrupt line skipped
            self.assertTrue(reloaded.verify())


class C3PoisonRelationTests(unittest.TestCase):
    def _mem(self):
        from cognitive_memory.reliability import GovernedMemory

        return GovernedMemory(policy={
            "name": "p", "detect_sensitive": False,
            "source_trust": {"scraper": 0.1, "recruiter": 0.95}, "trust_margin": 0.5,
        })

    def test_cross_relation_poison_not_served(self):
        mem = self._mem()
        mem.remember("cand_1 salary 120k", subject="cand_1", relation="salary",
                     object="120k", tenant="t", entity="cand_1", source="recruiter", trust=0.95)
        # poison: same entity + queryable tokens, but a DIFFERENT relation string
        mem.remember("cand_1 salary 40k", subject="cand_1", relation="salary_note",
                     object="40k", tenant="t", entity="cand_1", source="scraper", trust=0.1)
        result = mem.recall_value("cand_1 salary", tenant="t", entity="cand_1")
        self.assertNotEqual(result.answer, "40k", "low-trust cross-relation poison was served")

    def test_benign_same_source_multi_relation_still_served(self):
        from cognitive_memory.reliability import GovernedMemory

        mem = GovernedMemory(policy={"name": "p", "detect_sensitive": False})
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice", source="user", trust=0.9)
        mem.remember("alice location berlin", subject="alice", relation="location",
                     object="berlin", tenant="t", entity="alice", source="user", trust=0.9)
        self.assertEqual(mem.recall_value("alice salary", tenant="t", entity="alice").answer, "120k")


if __name__ == "__main__":
    unittest.main()
