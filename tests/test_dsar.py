"""Acceptance tests for the DSAR request model (the ITSM-facing contract).

DSARRequest is the single canonical entry shape for the MCP tools, the
REST gateway and the pull listeners. Validation is deliberately stricter
than GovernedMemory.forget: the ITSM channel always knows an acting
requester, so requester is mandatory for every kind.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest

from cognitive_memory.dsar import KINDS, DSARRequest, DSARService
from cognitive_memory.mcp_server import GovernedMemoryService, ServerConfig
from cognitive_memory.reliability import MemoryRecord, Scope
from cognitive_memory.signing import canonical_json


def _valid(**overrides):
    data = {
        "request_id": "req-1",
        "kind": "erasure",
        "tenant": "acme",
        "requester": "dpo@acme.example",
        "term": "alice",
    }
    data.update(overrides)
    return data


class DSARRequestValidationTests(unittest.TestCase):
    def test_valid_erasure_request_exposes_all_fields(self):
        req = DSARRequest(**_valid(ticket="RITM0010001"))
        self.assertEqual(req.kind, "erasure")
        self.assertEqual(req.tenant, "acme")
        self.assertEqual(req.requester, "dpo@acme.example")
        self.assertEqual(req.term, "alice")
        self.assertEqual(req.ticket, "RITM0010001")
        self.assertEqual(req.source, "itsm")

    def test_kinds_tuple_is_the_public_contract(self):
        self.assertEqual(KINDS, ("erasure", "consent_withdrawal", "access"))
        with self.assertRaises(ValueError):
            DSARRequest(**_valid(kind="purge"))

    def test_requester_is_mandatory_for_every_kind(self):
        for kind in KINDS:
            data = _valid(kind=kind, requester="")
            if kind == "access":
                data["subject"] = "alice"
            with self.assertRaises(ValueError):
                DSARRequest(**data)

    def test_erasure_and_consent_withdrawal_require_term(self):
        for kind in ("erasure", "consent_withdrawal"):
            with self.assertRaises(ValueError):
                DSARRequest(**_valid(kind=kind, term=""))

    def test_access_requires_subject(self):
        req = DSARRequest(**_valid(kind="access", term="", subject="alice"))
        self.assertEqual(req.subject, "alice")
        with self.assertRaises(ValueError):
            DSARRequest(**_valid(kind="access", term="", subject=" "))

    def test_purpose_is_only_allowed_for_consent_withdrawal(self):
        req = DSARRequest(**_valid(kind="consent_withdrawal", purpose="marketing"))
        self.assertEqual(req.purpose, "marketing")
        with self.assertRaises(ValueError):
            DSARRequest(**_valid(purpose="marketing"))


class FromDictTests(unittest.TestCase):
    def test_from_dict_generates_request_id_only_when_absent(self):
        data = _valid()
        del data["request_id"]
        first = DSARRequest.from_dict(data)
        second = DSARRequest.from_dict(data)
        self.assertEqual(len(first.request_id), 32)
        self.assertNotEqual(first.request_id, second.request_id)
        stable = DSARRequest.from_dict(_valid(request_id="RITM0010001"))
        self.assertEqual(stable.request_id, "RITM0010001")
        with self.assertRaises(ValueError):
            DSARRequest(**_valid(request_id="  "))

    def test_from_dict_strips_casts_and_ignores_unknown_keys(self):
        data = _valid(request_id=" req-9 ", term=" alice ")
        data["sys_id"] = "abc123"
        data["number"] = 42
        req = DSARRequest.from_dict(data)
        self.assertEqual(req.request_id, "req-9")
        self.assertEqual(req.term, "alice")
        round_tripped = req.to_dict()
        self.assertNotIn("sys_id", round_tripped)
        self.assertEqual(
            set(round_tripped),
            {"request_id", "kind", "tenant", "requester", "term", "subject",
             "purpose", "ticket", "source"},
        )
        json.dumps(round_tripped)
        with self.assertRaises(ValueError):
            DSARRequest.from_dict(["not", "a", "dict"])


def _seeded_service(tenant="acme"):
    svc = GovernedMemoryService()
    mem = svc.memory_for(tenant)
    mem.remember("alice likes tea", subject="alice", relation="likes",
                 object="tea", tenant=tenant, source="crm")
    mem.remember("alice works at initech", subject="alice", relation="works_at",
                 object="initech", tenant=tenant, source="hr")
    mem.remember("bob likes coffee", subject="bob", relation="likes",
                 object="coffee", tenant=tenant, source="crm")
    return svc, mem


def _strict_service(tenant="strict_co"):
    config = ServerConfig.from_dict({
        "tenant_profiles": {
            tenant: {"name": "strict", "strict_revocation": True,
                     "revocation_operators": ["dpo_admin"]},
        },
    })
    svc = GovernedMemoryService(config)
    mem = svc.memory_for(tenant)
    mem.remember("alice likes tea", subject="alice", relation="likes",
                 object="tea", tenant=tenant, source="crm")
    return svc, mem


class _NamedStore:
    name = "embeddings"

    def purge_records(self, records):
        return len(list(records))


class DSARPlanTests(unittest.TestCase):
    def test_plan_erasure_reports_matches_sources_and_derivatives(self):
        svc, mem = _seeded_service()
        mem.register_derivative_store(_NamedStore())
        alice_ids = sorted(
            r.id for r in mem.backend.all_records() if r.subject == "alice"
        )
        report = DSARService(svc).plan(DSARRequest(
            request_id="req-1", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="alice",
        ))
        self.assertEqual(report["kind"], "erasure")
        self.assertEqual(report["tenant"], "acme")
        self.assertEqual(report["term"], "alice")
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(report["matched_ids"], alice_ids)
        self.assertEqual(report["sources"], ["crm", "hr"])
        self.assertEqual(report["derivative_stores"], ["embeddings"])
        self.assertEqual(report["would_hold"], "")

    def test_plan_is_read_only(self):
        svc, mem = _strict_service()
        before = sorted(r.id for r in mem.backend.all_records())
        DSARService(svc).plan(DSARRequest(
            request_id="req-2", kind="erasure", tenant="strict_co",
            requester="mallory", term="alice",
        ))
        self.assertEqual(sorted(r.id for r in mem.backend.all_records()), before)
        self.assertEqual(mem.erased_terms.get("strict_co", []), [])
        self.assertEqual(mem.list_pending_revocations("strict_co"), [])

    def test_plan_is_deterministic(self):
        svc, _ = _seeded_service()
        request = DSARRequest(
            request_id="req-3", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="alice",
        )
        dsar = DSARService(svc)
        self.assertEqual(dsar.plan(request), dsar.plan(request))

    def test_plan_reports_would_hold_under_strict_profile(self):
        svc, _ = _strict_service()
        dsar = DSARService(svc)
        held = dsar.plan(DSARRequest(
            request_id="req-4", kind="erasure", tenant="strict_co",
            requester="mallory", term="alice",
        ))
        self.assertEqual(held["would_hold"], "unauthorized_requester")
        cleared = dsar.plan(DSARRequest(
            request_id="req-5", kind="erasure", tenant="strict_co",
            requester="dpo_admin", term="alice",
        ))
        self.assertEqual(cleared["would_hold"], "")

    def test_plan_with_empty_tokens_yields_zero_matches(self):
        svc, _ = _seeded_service()
        report = DSARService(svc).plan(DSARRequest(
            request_id="req-6", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="---",
        ))
        self.assertEqual(report["matched_count"], 0)
        self.assertEqual(report["matched_ids"], [])

    def test_plan_consent_withdrawal_matches_like_erasure(self):
        svc, _ = _seeded_service()
        dsar = DSARService(svc)
        erasure = dsar.plan(DSARRequest(
            request_id="req-7", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="alice",
        ))
        consent = dsar.plan(DSARRequest(
            request_id="req-8", kind="consent_withdrawal", tenant="acme",
            requester="dpo@acme.example", term="alice", purpose="marketing",
        ))
        self.assertEqual(consent["kind"], "consent_withdrawal")
        self.assertEqual(consent["matched_ids"], erasure["matched_ids"])

    def test_plan_access_counts_active_and_quarantined(self):
        svc, mem = _seeded_service()
        mem.backend.write(MemoryRecord(
            subject="alice", relation="ssn", object="123-45",
            scope=Scope(tenant="acme", subject=""), quarantined=True,
            quarantine_reason="low_trust", id="q1",
        ))
        report = DSARService(svc).plan(DSARRequest(
            request_id="req-9", kind="access", tenant="acme",
            requester="dpo@acme.example", subject="alice",
        ))
        self.assertEqual(report["kind"], "access")
        self.assertEqual(report["subject"], "alice")
        self.assertEqual(report["record_count"], 2)
        self.assertEqual(report["quarantined_count"], 1)

    def test_plan_audits_counts_but_never_record_content(self):
        svc, mem = _seeded_service()
        DSARService(svc).plan(DSARRequest(
            request_id="req-10", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="alice",
        ))
        entries = mem.audit.filter("dsar_plan")
        self.assertEqual(len(entries), 1)
        details = entries[0].details
        self.assertEqual(details["request"]["request_id"], "req-10")
        self.assertEqual(details["matched_count"], 2)
        serialized = canonical_json(details)
        self.assertNotIn("likes tea", serialized)
        self.assertNotIn("initech", serialized)
        self.assertTrue(mem.verify_audit())


class DSARExecuteTests(unittest.TestCase):
    def _erasure_request(self, request_id="req-e1", **overrides):
        data = {
            "request_id": request_id, "kind": "erasure", "tenant": "acme",
            "requester": "dpo@acme.example", "term": "alice",
            "ticket": "RITM0010001",
        }
        data.update(overrides)
        return DSARRequest(**data)

    def test_execute_erasure_removes_and_returns_certificate(self):
        svc, mem = _seeded_service()
        result = DSARService(svc).execute(self._erasure_request())
        self.assertEqual(result["status"], "executed")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["removed"], 2)
        cert = result["certificate"]
        self.assertEqual(cert["details"]["targeted_count"], 2)
        self.assertEqual(cert["details"]["requester"], "dpo@acme.example")
        remaining = [r.subject for r in mem.backend.all_records()]
        self.assertEqual(remaining, ["bob"])
        entry = mem.audit.filter("dsar_execute")[-1]
        self.assertEqual(entry.details["certificate_seq"], cert["seq"])

    def test_execute_zero_match_erasure_is_still_executed(self):
        svc, mem = _seeded_service()
        result = DSARService(svc).execute(
            self._erasure_request(request_id="req-e2", term="zzz")
        )
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["certificate"]["details"]["targeted_count"], 0)
        self.assertEqual(len(mem.backend.all_records()), 3)

    def test_execute_consent_withdrawal_registers_revocation(self):
        svc, mem = _seeded_service()
        result = DSARService(svc).execute(DSARRequest(
            request_id="req-c1", kind="consent_withdrawal", tenant="acme",
            requester="dpo@acme.example", term="alice", purpose="marketing",
        ))
        self.assertEqual(result["status"], "executed")
        self.assertNotIn("certificate", result)
        purposes = [p for _, p in mem.revoked_consent.get("acme", [])]
        self.assertIn("marketing", purposes)
        entry = mem.audit.filter("dsar_execute")[-1]
        self.assertEqual(entry.details["kind"], "consent_withdrawal")
        self.assertNotIn("certificate_seq", entry.details)

    def test_execute_held_under_strict_profile_deletes_nothing(self):
        svc, mem = _strict_service()
        result = DSARService(svc).execute(DSARRequest(
            request_id="req-h1", kind="erasure", tenant="strict_co",
            requester="mallory", term="alice",
        ))
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["reason"], "unauthorized_requester")
        self.assertTrue(result["pending_id"])
        self.assertEqual(len(mem.backend.all_records()), 1)
        entry = mem.audit.filter("dsar_execute")[-1]
        self.assertEqual(entry.details["status"], "held")

    def test_execute_is_idempotent_within_one_instance(self):
        svc, mem = _seeded_service()
        dsar = DSARService(svc)
        first = dsar.execute(self._erasure_request())
        replay = dsar.execute(self._erasure_request())
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "executed")
        self.assertEqual(len(mem.audit.filter("erasure")), 1)
        self.assertEqual(len(mem.audit.filter("dsar_execute")), 1)
        self.assertEqual(
            replay["certificate"]["seq"], first["certificate"]["seq"]
        )

    def test_execute_registry_rebuilds_from_audit(self):
        svc, mem = _seeded_service()
        first = DSARService(svc).execute(self._erasure_request())
        replay = DSARService(svc).execute(self._erasure_request())
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            replay["certificate"]["details"]["targeted_count"], 2
        )
        self.assertEqual(replay["certificate"]["seq"], first["certificate"]["seq"])
        self.assertEqual(len(mem.audit.filter("erasure")), 1)
        self.assertEqual(len(mem.audit.filter("dsar_execute")), 1)

    def test_execute_audit_details_carry_ticket_and_chain_verifies(self):
        svc, mem = _seeded_service()
        DSARService(svc).execute(self._erasure_request())
        details = mem.audit.filter("dsar_execute")[-1].details
        self.assertEqual(details["request_id"], "req-e1")
        self.assertEqual(details["kind"], "erasure")
        self.assertEqual(details["status"], "executed")
        self.assertEqual(details["ticket"], "RITM0010001")
        self.assertIsInstance(details["certificate_seq"], int)
        self.assertTrue(mem.verify_audit())


class DSARAccessExportTests(unittest.TestCase):
    def _access_request(self, request_id="req-a1", subject="alice", **overrides):
        data = {
            "request_id": request_id, "kind": "access", "tenant": "acme",
            "requester": "dpo@acme.example", "subject": subject,
            "ticket": "RITM0010002",
        }
        data.update(overrides)
        return DSARRequest(**data)

    def test_access_export_returns_subject_records_and_pinned_hash(self):
        svc, mem = _seeded_service()
        mem.backend.write(MemoryRecord(
            subject="alice", relation="ssn", object="123-45",
            scope=Scope(tenant="acme", subject=""), quarantined=True,
            quarantine_reason="low_trust", id="q1",
        ))
        result = DSARService(svc).execute(self._access_request())
        self.assertEqual(result["status"], "executed")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["withheld_count"], 1)
        self.assertEqual(result["withheld"], [{"id": "q1", "reason": "low_trust"}])
        ids = [r["id"] for r in result["records"]]
        self.assertEqual(ids, sorted(ids))
        texts = {r["text"] for r in result["records"]}
        self.assertIn("alice likes tea", texts)
        for record in result["records"]:
            self.assertEqual(
                set(record),
                {"id", "subject", "relation", "object", "text", "source",
                 "provenance", "allowed_purposes", "consented_purposes"},
            )
        expected = hashlib.sha256(canonical_json(
            {"records": result["records"], "withheld": result["withheld"]}
        ).encode("utf-8")).hexdigest()
        self.assertEqual(result["package_sha256"], expected)

    def test_access_export_is_never_persisted_in_audit(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        config = ServerConfig.from_dict({"audit_path": os.path.join(tmp, "audit")})
        svc = GovernedMemoryService(config)
        mem = svc.memory_for("acme")
        mem.remember("alice likes tea", subject="alice", relation="likes",
                     object="tea", tenant="acme", source="crm")
        DSARService(svc).execute(self._access_request())
        with open(os.path.join(tmp, "audit.acme.jsonl"), encoding="utf-8") as handle:
            raw = handle.read()
        actions = [json.loads(line)["action"] for line in raw.splitlines()]
        self.assertIn("dsar_execute", actions)
        self.assertIn("access_export", actions)
        self.assertIn("package_sha256", raw)
        self.assertNotIn("likes tea", raw)
        self.assertNotIn("tea", raw)

    def test_access_export_for_unknown_subject_is_valid_and_empty(self):
        svc, _ = _seeded_service()
        result = DSARService(svc).execute(self._access_request(subject="nobody"))
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["withheld"], [])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["withheld_count"], 0)
        self.assertTrue(result["package_sha256"])

    def test_access_replay_rederives_without_drift(self):
        svc, mem = _seeded_service()
        dsar = DSARService(svc)
        first = dsar.execute(self._access_request())
        replay = dsar.execute(self._access_request())
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["hash_drift"])
        self.assertEqual(replay["package_sha256"], first["package_sha256"])
        self.assertEqual(len(replay["records"]), 2)
        self.assertEqual(len(mem.audit.filter("dsar_execute")), 1)
        self.assertEqual(len(mem.audit.filter("access_export")), 1)

    def test_access_replay_detects_hash_drift(self):
        svc, mem = _seeded_service()
        dsar = DSARService(svc)
        first = dsar.execute(self._access_request())
        mem.remember("alice hates mondays", subject="alice", relation="hates",
                     object="mondays", tenant="acme", source="crm")
        replay = dsar.execute(self._access_request())
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["hash_drift"])
        self.assertEqual(replay["recorded_package_sha256"], first["package_sha256"])
        self.assertNotEqual(replay["package_sha256"], first["package_sha256"])

    def test_access_after_erasure_returns_empty_package(self):
        svc, _ = _seeded_service()
        dsar = DSARService(svc)
        dsar.execute(DSARRequest(
            request_id="req-e9", kind="erasure", tenant="acme",
            requester="dpo@acme.example", term="alice",
        ))
        result = dsar.execute(self._access_request(request_id="req-a9"))
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()
