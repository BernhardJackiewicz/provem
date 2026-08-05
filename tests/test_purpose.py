"""Purpose limitation: the same fact can be allowed for one task and
forbidden for another.

The declared purpose is an untrusted input on the read path; policy decides.
No declared purpose (None or empty) means no purpose gating, which keeps every
existing call site and the frozen benchmark byte-identical. Records carry
allowed_purposes (use-side allowlist) and consented_purposes (what the data
subject agreed to); empty tuples mean unrestricted.
"""

import unittest

from cognitive_memory.reliability import (
    CORRECT,
    GovernedMemory,
    IngestTurn,
    MemoryRecord,
    QueryTurn,
    Scenario,
    Scope,
    run_trajectory,
)


def _remember_scoped(mem, allowed=(), consented=(), tenant="t", subject="alice"):
    mem.remember("alice salary 120k", subject="alice", relation="salary",
                 object="120k", tenant=tenant, entity=subject,
                 allowed_purposes=allowed, consented_purposes=consented)


class PurposeFieldTests(unittest.TestCase):
    def test_query_turn_purpose_defaults_none(self):
        turn = QueryTurn("q", Scope(), None)
        self.assertIsNone(turn.purpose)

    def test_record_purpose_fields_default_empty(self):
        record = MemoryRecord("s", "r", "o", Scope())
        self.assertEqual(record.allowed_purposes, ())
        self.assertEqual(record.consented_purposes, ())


class PurposeGateTests(unittest.TestCase):
    def test_no_declared_purpose_is_unrestricted(self):
        mem = GovernedMemory()
        _remember_scoped(mem, allowed=("scheduling",))
        result = mem.recall_value("alice salary", tenant="t", entity="alice")
        self.assertFalse(result.abstained)

    def test_allowed_purpose_serves(self):
        mem = GovernedMemory()
        _remember_scoped(mem, allowed=("scheduling",))
        result = mem.recall_value("alice salary", tenant="t", entity="alice", purpose="scheduling")
        self.assertFalse(result.abstained)
        self.assertEqual(result.answer, "120k")

    def test_disallowed_purpose_refuses_machine_readably(self):
        mem = GovernedMemory()
        _remember_scoped(mem, allowed=("scheduling",))
        result = mem.recall_value("alice salary", tenant="t", entity="alice", purpose="hiring")
        self.assertTrue(result.abstained)
        self.assertIn("purpose_mismatch", {reason for _, reason in result.excluded})

    def test_unrestricted_record_served_under_any_purpose(self):
        mem = GovernedMemory()
        _remember_scoped(mem)
        result = mem.recall_value("alice salary", tenant="t", entity="alice", purpose="hiring")
        self.assertFalse(result.abstained)

    def test_purpose_rule_category_gate(self):
        policy = {"name": "p", "purpose_rules": {"hiring": {"allowed_relations": ["seniority"]}}}
        blocked_mem = GovernedMemory(policy=policy)
        _remember_scoped(blocked_mem)
        blocked = blocked_mem.recall_value("alice salary", tenant="t", entity="alice", purpose="hiring")
        self.assertTrue(blocked.abstained)
        self.assertIn("purpose_mismatch", {reason for _, reason in blocked.excluded})
        served_mem = GovernedMemory(policy=policy)
        served_mem.remember("alice seniority senior", subject="alice", relation="seniority",
                            object="senior", tenant="t", entity="alice")
        served = served_mem.recall_value("alice seniority", tenant="t", entity="alice", purpose="hiring")
        self.assertFalse(served.abstained)
        self.assertEqual(served.answer, "senior")

    def test_purpose_rule_requires_consent(self):
        policy = {"name": "p", "purpose_rules": {"research": {"require_consent": True}}}
        mem = GovernedMemory(policy=policy)
        _remember_scoped(mem)
        blocked = mem.recall_value("alice salary", tenant="t", entity="alice", purpose="research")
        self.assertTrue(blocked.abstained)
        mem2 = GovernedMemory(policy=policy)
        _remember_scoped(mem2, consented=("research",))
        served = mem2.recall_value("alice salary", tenant="t", entity="alice", purpose="research")
        self.assertFalse(served.abstained)

    def test_purpose_block_is_audited(self):
        mem = GovernedMemory()
        _remember_scoped(mem, allowed=("scheduling",))
        mem.recall_value("alice salary", tenant="t", entity="alice", purpose="hiring")
        blocked = mem.audit.filter("recall_blocked")
        self.assertTrue(blocked)
        self.assertIn("purpose_mismatch", blocked[-1].details["reasons"])

    def test_trajectory_forwards_purpose(self):
        # run_trajectory scrubs QueryTurns before replay; the declared purpose
        # must survive the scrub or every purpose scenario dies silently.
        mem = GovernedMemory()
        scope = Scope("t", "alice")
        scenario = Scenario(
            scenario_id="purpose_fwd",
            ingest=[IngestTurn("fact", "alice salary 120k", "alice", "salary", "120k",
                               scope, "user", 0.95, allowed_purposes=("scheduling",))],
            queries=[QueryTurn("alice salary", scope, None, "purpose", purpose="hiring")],
            family="purpose",
        )
        trajectory = run_trajectory(mem, scenario)
        step = trajectory.steps[0]
        self.assertIsNone(step.got, "purpose was dropped by the trajectory scrub")
        self.assertEqual(step.outcome, CORRECT)


class PrototypePurposeTests(unittest.TestCase):
    def setUp(self):
        from cognitive_memory.controller import MemoryController
        from cognitive_memory.retrieval import RetrievalPlanner

        self.controller = MemoryController()
        self.retrieval = RetrievalPlanner(self.controller.store, self.controller.policy)

    def _ingest_fact(self):
        from datetime import datetime, timezone

        from cognitive_memory.models import Episode

        self.controller.ingest_episode(
            Episode("FACT user|target_rate|120k", timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
        )
        return [f for f in self.controller.store.list_facts() if f.object == "120k"][0]

    def test_retrieval_request_purpose_defaults_none(self):
        from cognitive_memory.models import RetrievalRequest

        self.assertIsNone(RetrievalRequest(query="q").purpose)

    def test_fact_purpose_mismatch_excluded(self):
        from cognitive_memory.models import RetrievalRequest

        fact = self._ingest_fact()
        fact.allowed_purposes = ["scheduling"]
        result = self.retrieval.retrieve(RetrievalRequest(query="target rate", purpose="hiring"))
        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.reason == "purpose_mismatch" for item in result.excluded_memories))

    def test_fact_served_under_allowed_purpose(self):
        from cognitive_memory.models import RetrievalRequest

        fact = self._ingest_fact()
        fact.allowed_purposes = ["scheduling"]
        result = self.retrieval.retrieve(RetrievalRequest(query="target rate", purpose="scheduling"))
        self.assertIn("120k", result.answer_text())

    def test_event_purpose_mismatch_excluded(self):
        from datetime import datetime, timezone

        from cognitive_memory.models import MemoryEvent, RetrievalRequest

        event = MemoryEvent("meeting", "call with alice",
                            datetime(2026, 1, 1, tzinfo=timezone.utc),
                            evidence_episode_ids=["ep1"],
                            allowed_purposes=["scheduling"])
        blocked = self.controller.policy.exclusion_reason(
            event, RetrievalRequest(query="call", purpose="hiring"))
        self.assertEqual(blocked, "purpose_mismatch")
        served = self.controller.policy.exclusion_reason(
            event, RetrievalRequest(query="call", purpose="scheduling"))
        self.assertIsNone(served)


if __name__ == "__main__":
    unittest.main()
