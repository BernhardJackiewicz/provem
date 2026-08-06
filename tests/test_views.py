"""Short-lived scoped memory views (capabilities).

An approval to retrieve under (tenant, subject, purpose) can be minted as a
TTL-bound view handle. A later check answers "is this authorization still
valid", and any governance change in the tenant (forget, restrict, consent
withdrawal, policy swap) invalidates outstanding views: fail-safe,
tenant-coarse re-validation. The dead Scope.session field carries the handle
on recall; unknown session strings keep their historical no-op behaviour.
"""

import unittest
from datetime import datetime, timedelta, timezone

from cognitive_memory.reliability import GovernedMemory, Scope


class _Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


def _mem():
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    mem = GovernedMemory(now_fn=clock)
    return mem, clock


class ViewTests(unittest.TestCase):
    def test_issue_view_returns_handle_and_audits(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        self.assertTrue(handle)
        issued = mem.audit.filter("view_issued")
        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].details["tenant"], "t")
        self.assertEqual(issued[0].details["purpose"], "scheduling")

    def test_validate_within_ttl_valid(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        self.assertEqual(mem.validate_view(handle)["status"], "valid")

    def test_validate_after_ttl_expired(self):
        mem, clock = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        clock.advance(601)
        self.assertEqual(mem.validate_view(handle)["status"], "expired")

    def test_unknown_handle_unknown(self):
        mem, _ = _mem()
        self.assertEqual(mem.validate_view("nope")["status"], "unknown")

    def test_forget_invalidates_tenant_views(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        mem.forget("anything", Scope(tenant="t"))
        self.assertEqual(mem.validate_view(handle)["status"], "revoked")

    def test_revoke_consent_invalidates_views(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        mem.revoke_consent("anything", Scope(tenant="t"))
        self.assertEqual(mem.validate_view(handle)["status"], "revoked")

    def test_restrict_invalidates_tenant_views(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        mem.restrict("anything", Scope(tenant="t"))
        self.assertEqual(mem.validate_view(handle)["status"], "revoked")

    def test_set_policy_invalidates_all_views(self):
        # a policy swap changes the rules every outstanding approval was
        # minted under; the policy-wide epoch revokes them all
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        mem.set_policy({"name": "swapped"})
        self.assertEqual(mem.validate_view(handle)["status"], "revoked")

    def test_other_tenant_governance_does_not_invalidate(self):
        mem, _ = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        mem.forget("anything", Scope(tenant="other"))
        self.assertEqual(mem.validate_view(handle)["status"], "valid")

    def test_denial_is_audited_and_chain_verifies(self):
        mem, clock = _mem()
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        clock.advance(601)
        mem.validate_view(handle)
        self.assertTrue(mem.audit.filter("view_denied"))
        self.assertTrue(mem.verify_audit())


class ViewBoundRecallTests(unittest.TestCase):
    def test_recall_with_expired_view_abstains(self):
        mem, clock = _mem()
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice")
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        clock.advance(601)
        result = mem.recall_value("alice salary", tenant="t", entity="alice", session=handle)
        self.assertTrue(result.abstained)
        self.assertEqual(result.reason, "view_expired")

    def test_recall_with_valid_view_inherits_purpose(self):
        mem, _ = _mem()
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice",
                     allowed_purposes=("hiring",))
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        result = mem.recall_value("alice salary", tenant="t", entity="alice", session=handle)
        self.assertTrue(result.abstained, "view-bound recall must be gated by the view's purpose")
        self.assertIn("purpose_mismatch", {reason for _, reason in result.excluded})

    def test_recall_with_mismatched_declared_purpose_refused(self):
        mem, _ = _mem()
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice")
        handle = mem.issue_view(tenant="t", subject="alice", purpose="scheduling", ttl_seconds=600)
        result = mem.recall_value("alice salary", tenant="t", entity="alice",
                                  session=handle, purpose="hiring")
        self.assertTrue(result.abstained)
        self.assertEqual(result.reason, "view_scope_mismatch")

    def test_unknown_session_string_is_a_noop(self):
        # Both adapters persist Scope.session; arbitrary session strings must
        # keep their historical no-op behaviour.
        mem, _ = _mem()
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice")
        result = mem.recall_value("alice salary", tenant="t", entity="alice", session="legacy-session-42")
        self.assertFalse(result.abstained)


if __name__ == "__main__":
    unittest.main()
