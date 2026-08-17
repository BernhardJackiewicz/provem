"""Acceptance tests for the DSAR request model (the ITSM-facing contract).

DSARRequest is the single canonical entry shape for the MCP tools, the
REST gateway and the pull listeners. Validation is deliberately stricter
than GovernedMemory.forget: the ITSM channel always knows an acting
requester, so requester is mandatory for every kind.
"""

import json
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


if __name__ == "__main__":
    unittest.main()
