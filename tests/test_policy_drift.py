"""Ordered scenario steps and mid-trajectory state changes.

run_trajectory replayed all writes, then all reads: nothing could change
state between two retrieval steps, so policy-drift and purpose-transition
scenarios were inexpressible. Scenario.steps (optional, default None) is an
ordered list of IngestTurn/QueryTurn dispatched in order; steps=None keeps
the exact legacy two-phase replay, so the frozen headline mixture is
untouched.
"""

import unittest

from cognitive_memory.reliability import (
    CORRECT,
    GovernedMemory,
    IngestTurn,
    QueryTurn,
    RecallResult,
    Scenario,
    Scope,
    run_trajectory,
)
from cognitive_memory.reliability_suite import generate_scenarios


def _fact(subject, relation, obj, scope, text=None):
    return IngestTurn("fact", text or ("%s %s %s" % (subject, relation, obj)),
                      subject, relation, obj, scope, "user", 0.95)


class _CapturingMemory:
    """Records the QueryTurn view run_trajectory hands to recall."""

    def __init__(self):
        self.ingested = []
        self.recall_views = []

    def ingest(self, turn):
        self.ingested.append(turn)

    def recall(self, turn):
        self.recall_views.append(turn)
        return RecallResult(answer=None, abstained=True, reason="no_match")


class ScenarioStepsTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope("t", "alex_1")

    def test_scenario_defaults_have_no_steps(self):
        scenario = Scenario("s", [], [], "benign")
        self.assertIsNone(scenario.steps)

    def test_steps_none_matches_legacy_replay(self):
        ingest = [
            _fact("alex_1", "salary", "120k", self.scope, text="alex salary 120k"),
            _fact("alex_1", "location", "berlin", self.scope, text="alex location berlin"),
        ]
        queries = [
            QueryTurn("alex salary", self.scope, "120k", "benign"),
            QueryTurn("alex location", self.scope, "berlin", "benign"),
        ]
        legacy = run_trajectory(GovernedMemory(), Scenario("legacy", ingest, queries, "benign"))
        stepped = run_trajectory(
            GovernedMemory(),
            Scenario("stepped", [], [], "benign", steps=list(ingest) + list(queries)),
        )
        self.assertEqual(legacy.steps, stepped.steps)
        self.assertEqual(legacy.ops, stepped.ops)

    def test_interleaved_steps_dispatch_in_order(self):
        # State changes between reads: impossible with the two-phase replay.
        steps = [
            _fact("alex_1", "salary", "120k", self.scope, text="alex salary 120k"),
            QueryTurn("alex salary", self.scope, "120k", "benign"),
            _fact("alex_1", "salary", "130k", self.scope, text="alex salary 130k"),
            QueryTurn("alex salary", self.scope, "130k", "benign"),
        ]
        trajectory = run_trajectory(GovernedMemory(), Scenario("mid", [], [], "benign", steps=steps))
        self.assertEqual([s.outcome for s in trajectory.steps], [CORRECT, CORRECT])
        self.assertEqual([s.got for s in trajectory.steps], ["120k", "130k"])

    def test_scrubbed_view_forwards_purpose_and_hides_labels(self):
        memory = _CapturingMemory()
        steps = [QueryTurn("alex salary", self.scope, "120k", "purpose", purpose="hiring")]
        run_trajectory(memory, Scenario("scrub", [], [], "purpose", steps=steps))
        view = memory.recall_views[0]
        self.assertIsNone(view.expected, "ground truth leaked to the memory layer")
        self.assertEqual(view.failure_class, "benign")
        self.assertEqual(view.purpose, "hiring")

    def test_headline_scenarios_have_no_steps(self):
        for scenario in generate_scenarios(1, 96):
            self.assertIsNone(scenario.steps, "headline mixture must stay on the legacy path")


class ConsentRevocationTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope("t", "alice")

    def _mem_with_fact(self, consented=("research", "treatment")):
        mem = GovernedMemory()
        mem.remember("alice condition data cd77", subject="alice", relation="condition",
                     object="cd77", tenant="t", entity="alice",
                     consented_purposes=consented)
        return mem

    def test_revoke_blocks_use_but_keeps_record(self):
        mem = self._mem_with_fact()
        mem.revoke_consent("cd77", Scope(tenant="t"))
        result = mem.recall_value("alice condition cd77", tenant="t", entity="alice")
        self.assertTrue(result.abstained)
        self.assertIn("consent_revoked", {reason for _, reason in result.excluded})
        # revocation is not erasure: the record stays in the store
        self.assertTrue(any("cd77" in r.text for r in mem.backend.all_records()))

    def test_revocation_scoped_to_purpose(self):
        mem = self._mem_with_fact()
        mem.revoke_consent("cd77", Scope(tenant="t"), purpose="research")
        blocked = mem.recall_value("alice condition cd77", tenant="t", entity="alice", purpose="research")
        self.assertTrue(blocked.abstained)
        served = mem.recall_value("alice condition cd77", tenant="t", entity="alice", purpose="treatment")
        self.assertFalse(served.abstained)

    def test_blanket_revocation_blocks_purposeless_reads(self):
        mem = self._mem_with_fact()
        mem.revoke_consent("cd77", Scope(tenant="t"))
        result = mem.recall_value("alice condition cd77", tenant="t", entity="alice")
        self.assertTrue(result.abstained)

    def test_revocation_is_tenant_scoped(self):
        mem = GovernedMemory()
        for tenant in ("t", "u"):
            mem.remember("alice condition data cd77", subject="alice", relation="condition",
                         object="cd77", tenant=tenant, entity="alice")
        mem.revoke_consent("cd77", Scope(tenant="t"))
        other = mem.recall_value("alice condition cd77", tenant="u", entity="alice")
        self.assertFalse(other.abstained)

    def test_revocation_certificate_chains(self):
        mem = self._mem_with_fact()
        mem.revoke_consent("cd77", Scope(tenant="t"), purpose="research")
        entries = mem.audit.filter("consent_revocation")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].details["term"], "cd77")
        self.assertEqual(entries[0].details["purpose"], "research")
        self.assertTrue(mem.verify_audit())

    def test_ingest_kind_revocation_dispatches(self):
        mem = self._mem_with_fact()
        mem.ingest(IngestTurn("revocation", "alice withdraws consent for cd77",
                              "alice", "condition", "cd77", Scope(tenant="t"),
                              "user", 1.0, term="cd77", purpose="research"))
        blocked = mem.recall_value("alice condition cd77", tenant="t", entity="alice", purpose="research")
        self.assertTrue(blocked.abstained)

    def test_revocation_survives_restart_via_audit_replay(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            audit = str(Path(tmp) / "audit.jsonl")
            mem = GovernedMemory(audit_path=audit)
            mem.revoke_consent("cd77", Scope(tenant="t"), purpose="research")
            mem2 = GovernedMemory(audit_path=audit)  # restart
            entries = mem2.revoked_consent.get("t", [])
            self.assertTrue(entries, "revocation state lost across restart")

    def test_set_policy_rederives_thresholds(self):
        mem = GovernedMemory()
        mem.set_policy({"name": "drifted", "relevance_floor": 0.9, "trust_margin": 0.3})
        self.assertEqual(mem.relevance_floor, 0.9)
        self.assertEqual(mem.trust_margin, 0.3)
        self.assertEqual(mem.policy.name, "drifted")
        self.assertTrue(mem.audit.filter("policy_update"))


class PrototypeRevocationTests(unittest.TestCase):
    def test_policy_store_revoke_consent_excludes_fact(self):
        from datetime import datetime, timezone

        from cognitive_memory.controller import MemoryController
        from cognitive_memory.models import Episode, RetrievalRequest
        from cognitive_memory.retrieval import RetrievalPlanner

        controller = MemoryController()
        retrieval = RetrievalPlanner(controller.store, controller.policy)
        controller.ingest_episode(
            Episode("FACT user|study_code|cd77", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        controller.policy.revoke_consent("cd77", purpose="research")
        blocked = retrieval.retrieve(RetrievalRequest(query="study code", purpose="research"))
        self.assertEqual(blocked.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.reason == "consent_revoked" for item in blocked.excluded_memories))
        served = retrieval.retrieve(RetrievalRequest(query="study code", purpose="treatment"))
        self.assertIn("cd77", served.answer_text())

    def test_revocation_survives_policy_round_trip(self):
        from cognitive_memory.policy import PolicyStore

        policy = PolicyStore()
        policy.revoke_consent("cd77", purpose="research")
        restored = PolicyStore.from_dict(policy.to_dict())
        self.assertEqual(restored.revoked_consent_terms, policy.revoked_consent_terms)
        # old snapshots without the key load with the default empty set
        legacy = policy.to_dict()
        legacy.pop("revoked_consent_terms", None)
        self.assertEqual(PolicyStore.from_dict(legacy).revoked_consent_terms, set())


if __name__ == "__main__":
    unittest.main()
