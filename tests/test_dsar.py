"""Acceptance tests for the DSAR request model (the ITSM-facing contract).

DSARRequest is the single canonical entry shape for the MCP tools, the
REST gateway and the pull listeners. Validation is deliberately stricter
than GovernedMemory.forget: the ITSM channel always knows an acting
requester, so requester is mandatory for every kind.
"""

import json
import unittest

from cognitive_memory.dsar import KINDS, DSARRequest


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


if __name__ == "__main__":
    unittest.main()
