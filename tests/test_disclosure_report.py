"""PII-free disclosure report for subject-rights actions.

The tamper-evident audit chain retains the raw subject term on purpose: the
semantic-erasure feature needs it to catch paraphrases, so it cannot simply be
hashed away in place. The disclosure report is the separate artifact handed to
a DPO or regulator: it exposes only SHA-256 digests of the subject term and the
requester identity (SDM Baustein 60, M60.S06), while the internal chain and the
enforcement state stay unchanged.
"""

import hashlib
import json
import unittest

from cognitive_memory.audit import term_digest
from cognitive_memory.models import tokenize
from cognitive_memory.reliability import GovernedMemory, Scope


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DisclosureReportTests(unittest.TestCase):
    SUBJECT = "john smith"
    REQUESTER = "alice@dpo.example"
    RESTRICTED = "jane doe"
    REVOKED = "bob jones"

    def _populated(self):
        mem = GovernedMemory()
        scope = Scope(tenant="t")
        mem.forget(self.SUBJECT, scope, requester=self.REQUESTER)
        mem.restrict(self.RESTRICTED, scope)
        mem.revoke_consent(self.REVOKED, scope, purpose="marketing")
        return mem

    def test_term_digest_matches_plain_sha256(self):
        self.assertEqual(term_digest(self.SUBJECT), _sha(self.SUBJECT))
        self.assertEqual(term_digest(""), _sha(""))

    def test_report_carries_only_digests_not_raw_pii(self):
        mem = self._populated()
        report = mem.export_disclosure()
        blob = json.dumps(report)

        # No raw personal data anywhere in the artifact.
        for secret in (self.SUBJECT, self.REQUESTER, self.RESTRICTED, self.REVOKED):
            self.assertNotIn(secret, blob)

        self.assertEqual(report["kind"], "disclosure_report")
        self.assertEqual(report["schema"], "provem.disclosure.v1")
        self.assertEqual(len(report["entries"]), 3)

        by_action = {e["action"]: e for e in report["entries"]}
        self.assertEqual(set(by_action), {"erasure", "restrict", "consent_revocation"})

        erasure = by_action["erasure"]
        self.assertEqual(erasure["term_sha256"], _sha(self.SUBJECT))
        self.assertEqual(erasure["requester_sha256"], _sha(self.REQUESTER))
        self.assertIn("targeted_count", erasure)
        self.assertIn("backend_confirmed_deletes", erasure)
        # Raw identifiers must never survive into the report.
        self.assertNotIn("term", erasure)
        self.assertNotIn("requester", erasure)
        self.assertNotIn("removed_ids", erasure)

        self.assertEqual(by_action["restrict"]["term_sha256"], _sha(self.RESTRICTED))
        # No requester was supplied, so no requester digest is emitted.
        self.assertNotIn("requester_sha256", by_action["restrict"])

        revoke = by_action["consent_revocation"]
        self.assertEqual(revoke["term_sha256"], _sha(self.REVOKED))
        self.assertEqual(revoke["purpose"], "marketing")

    def test_report_anchors_to_the_live_chain_head(self):
        mem = self._populated()
        report = mem.export_disclosure()
        anchor = report["anchored_to_chain"]
        self.assertEqual(anchor["head_hash"], mem.audit.head_hash())
        self.assertEqual(anchor["count"], len(mem.audit.entries()))

    def test_internal_chain_is_unchanged_so_enforcement_survives(self):
        mem = self._populated()
        # The disclosure view does not mutate the tamper-evident log.
        self.assertTrue(mem.verify_audit())
        self.assertIn(self.SUBJECT, mem.export_audit(as_json=True))
        # The raw term is still retained where enforcement reads it (the whole
        # reason it cannot simply be hashed in place).
        self.assertIn(self.SUBJECT, mem.erased_term_texts.get("t", []))
        self.assertIn(tokenize(self.SUBJECT), mem.erased_terms.get("t", []))

    def test_json_export_is_valid_and_pii_free(self):
        mem = self._populated()
        text = mem.export_disclosure(as_json=True)
        parsed = json.loads(text)
        self.assertEqual(parsed, mem.export_disclosure())
        for secret in (self.SUBJECT, self.REQUESTER, self.RESTRICTED, self.REVOKED):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
