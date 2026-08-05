import unittest

from cognitive_memory.adapters.mem0_reliability import Mem0ReliabilityBackend
from cognitive_memory.reliability import (
    GovernedMemory,
    IngestTurn,
    MemoryBackend,
    MemoryRecord,
    QueryTurn,
    Scope,
)


class FakeMem0Client:
    """Minimal Mem0-shaped client. Rewrites memory text (as real Mem0 does) but
    preserves metadata, so the adapter must reconstruct from metadata."""

    def __init__(self, rewrite=True, refuse_delete=False, drop_first_delete=False):
        self.rewrite = rewrite
        self.refuse_delete = refuse_delete
        # a lagging backend: acks the first delete but does not remove
        self.drop_first_delete = drop_first_delete
        self._deletes_seen = 0
        self.memories = []
        self._n = 0

    def add(self, messages, user_id, metadata=None):
        self._n += 1
        content = " ".join(m.get("content", "") for m in messages)
        stored_text = ("summary: %s" % content) if self.rewrite else content
        mem = {
            "id": "mem_%d" % self._n,
            "memory": stored_text,   # deliberately paraphrased
            "score": 0.8,
            "metadata": dict(metadata or {}),
            "user_id": user_id,
        }
        self.memories.append(mem)
        return {"id": mem["id"]}

    def search(self, query, user_id=None, limit=25, **kwargs):
        user_id = user_id or (kwargs.get("filters") or {}).get("user_id")
        qtok = set(query.lower().replace("_", " ").split())
        out = []
        for mem in self.memories:
            if user_id and mem["user_id"] != user_id:
                continue
            # score against the ORIGINAL text in metadata (engram_text)
            base = mem["metadata"].get("engram_text", mem["memory"]).lower()
            if qtok & set(base.replace("_", " ").split()):
                out.append(mem)
        return out[:limit]

    def get_all(self, user_id=None, **kwargs):
        user_id = user_id or (kwargs.get("filters") or {}).get("user_id")
        return [m for m in self.memories if not user_id or m["user_id"] == user_id]

    def delete(self, memory_id=None):
        if self.refuse_delete:
            raise RuntimeError("backend refuses delete")
        self._deletes_seen += 1
        if self.drop_first_delete and self._deletes_seen == 1:
            return  # silent ack, nothing removed
        self.memories = [m for m in self.memories if m["id"] != memory_id]

    def delete_all(self, user_id=None, filters=None):
        uid = user_id or (filters or {}).get("user_id")
        self.memories = [m for m in self.memories if m["user_id"] != uid]
        return {"status": "ok"}


class Mem0AdapterTests(unittest.TestCase):
    def test_protocol_conformance(self):
        backend = Mem0ReliabilityBackend(client=FakeMem0Client())
        self.assertIsInstance(backend, MemoryBackend)

    def test_write_roundtrips_governance_metadata(self):
        client = FakeMem0Client(rewrite=True)
        backend = Mem0ReliabilityBackend(client=client)
        rec = MemoryRecord(subject="alice", relation="salary", object="120k",
                           scope=Scope(tenant="t1", subject="alice"), text="alice salary 120k",
                           source="user", trust=0.95, valid_at=1)
        backend.write(rec)
        # reconstruct via candidates; must recover object/subject from metadata,
        # not from the paraphrased "summary: ..." memory text
        cands = backend.candidates("alice salary", "t1")
        self.assertEqual(len(cands), 1)
        _, got = cands[0]
        self.assertEqual(got.object, "120k")
        self.assertEqual(got.subject, "alice")
        self.assertEqual(got.trust, 0.95)

    def test_tenant_maps_to_distinct_user_ids(self):
        client = FakeMem0Client()
        backend = Mem0ReliabilityBackend(client=client, run_namespace="ns")
        backend.write(MemoryRecord("a", "r", "1", Scope(tenant="t1"), text="a r 1"))
        backend.write(MemoryRecord("b", "r", "2", Scope(tenant="t2"), text="b r 2"))
        uids = {m["user_id"] for m in client.memories}
        self.assertEqual(uids, {"ns__t1", "ns__t2"})

    def test_governed_recall_over_mem0_backend(self):
        backend = Mem0ReliabilityBackend(client=FakeMem0Client())
        mem = GovernedMemory(backend=backend)
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t1", entity="alice")
        result = mem.recall_value("alice salary", tenant="t1", entity="alice")
        self.assertFalse(result.abstained)
        self.assertEqual(result.answer, "120k")

    def test_forget_blocks_value_even_when_backend_refuses_delete(self):
        # backend.delete raises -> delete_ids returns 0, but read-side erasure
        # must still filter the value out. This is the core "backend-agnostic
        # governance" property against a hostile/lagging store.
        client = FakeMem0Client(refuse_delete=True)
        backend = Mem0ReliabilityBackend(client=client)
        mem = GovernedMemory(backend=backend)
        mem.remember("alice secret42 health_note", subject="alice", relation="health_note",
                     object="secret42", tenant="t1", entity="alice")
        removed = mem.forget("secret42", Scope(tenant="t1", subject="alice"))
        self.assertEqual(removed, 0)  # backend refused
        result = mem.recall_value("alice health_note secret42", tenant="t1", entity="alice")
        self.assertTrue(result.abstained)  # governance still blocks it

    def test_scope_isolation_across_tenants(self):
        backend = Mem0ReliabilityBackend(client=FakeMem0Client())
        mem = GovernedMemory(backend=backend)
        mem.remember("shared detail alpha", subject="x", relation="detail",
                     object="alpha", tenant="tA", entity="x")
        mem.remember("shared detail beta", subject="x", relation="detail",
                     object="beta", tenant="tB", entity="x")
        a = mem.recall_value("shared detail", tenant="tA", entity="x")
        self.assertEqual(a.answer, "alpha")  # tenant tA never sees tB's value

    def test_purpose_metadata_round_trip(self):
        client = FakeMem0Client(rewrite=True)
        backend = Mem0ReliabilityBackend(client=client)
        rec = MemoryRecord(subject="alice", relation="salary", object="120k",
                           scope=Scope(tenant="t1", subject="alice"), text="alice salary 120k",
                           source="user", trust=0.95, valid_at=1,
                           allowed_purposes=("scheduling",), consented_purposes=("research",))
        backend.write(rec)
        _, got = backend.candidates("alice salary", "t1")[0]
        self.assertEqual(got.allowed_purposes, ("scheduling",))
        self.assertEqual(got.consented_purposes, ("research",))

    def test_missing_metadata_keys_default_empty(self):
        from cognitive_memory.adapters.mem0_reliability import (
            _metadata_to_record,
            _record_to_metadata,
        )

        rec = MemoryRecord("a", "r", "v", Scope(tenant="t"), text="a r v")
        meta = _record_to_metadata(rec)
        meta.pop("allowed_purposes", None)
        meta.pop("consented_purposes", None)
        restored = _metadata_to_record(meta, "t")
        self.assertEqual(restored.allowed_purposes, ())
        self.assertEqual(restored.consented_purposes, ())

    def test_get_by_ids_fetches_live_records(self):
        backend = Mem0ReliabilityBackend(client=FakeMem0Client())
        rid = backend.write(MemoryRecord("a", "r", "1", Scope(tenant="t1"), text="a r 1"))
        backend.write(MemoryRecord("b", "r", "2", Scope(tenant="t1"), text="b r 2"))
        got = backend.get_by_ids([rid, "missing_id"])
        self.assertEqual([r.id for r in got], [rid])

    def test_verify_erasure_resweeps_lagging_backend(self):
        client = FakeMem0Client(drop_first_delete=True)
        backend = Mem0ReliabilityBackend(client=client)
        rid = backend.write(MemoryRecord("bob", "note", "secret99", Scope(tenant="t"),
                                         text="bob secret99 note"))
        records = backend.get_by_ids([rid])
        backend.delete_ids([rid])  # silently dropped by the lagging backend
        out = backend.verify_erasure(records, "t")
        self.assertEqual(out["resweep_deleted"], 1)
        self.assertEqual(out["residual"], 0)
        self.assertEqual(client.memories, [], "resweep must actually remove the copy")

    def test_residuals_reported_when_backend_refuses(self):
        client = FakeMem0Client(refuse_delete=True)
        backend = Mem0ReliabilityBackend(client=client)
        rid = backend.write(MemoryRecord("bob", "note", "secret99", Scope(tenant="t"),
                                         text="bob secret99 note"))
        records = backend.get_by_ids([rid])
        backend.delete_ids([rid])
        out = backend.verify_erasure(records, "t")
        self.assertEqual(out["resweep_deleted"], 0)
        self.assertGreater(out["residual"], 0, "an unremovable copy must be reported, not hidden")

    def test_governed_forget_surfaces_backend_verification_in_certificate(self):
        client = FakeMem0Client(refuse_delete=True)
        backend = Mem0ReliabilityBackend(client=client)
        mem = GovernedMemory(backend=backend)
        mem.remember("bob secret99 note", subject="bob", relation="note",
                     object="secret99", tenant="t", entity="bob")
        mem.forget("secret99", Scope(tenant="t", subject="bob"))
        cert = mem.audit.filter("erasure")[-1]
        self.assertIn("backend_verification", cert.details)
        self.assertGreater(cert.details["backend_verification"]["residual"], 0)
        # read-side erasure still holds regardless of the residual copy
        self.assertTrue(mem.recall_value("bob note secret99", tenant="t", entity="bob").abstained)

    def test_purge_clears_run_tenants(self):
        client = FakeMem0Client()
        backend = Mem0ReliabilityBackend(client=client)
        backend.write(MemoryRecord("a", "r", "1", Scope(tenant="t1"), text="a r 1"))
        backend.write(MemoryRecord("b", "r", "2", Scope(tenant="t2"), text="b r 2"))
        backend.purge()
        self.assertEqual(client.memories, [])


if __name__ == "__main__":
    unittest.main()
