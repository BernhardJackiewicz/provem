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


class PurposeClassTests(unittest.TestCase):
    def test_purpose_leak_is_compliance_violation(self):
        from cognitive_memory.reliability import RecallResult, classify_step

        step = classify_step(QueryTurn("q", Scope(), None, "purpose"),
                             RecallResult(answer="x", abstained=False))
        self.assertTrue(step.compliance_violation)
        self.assertFalse(step.poisoning_success)

    def test_revocation_leak_is_compliance_violation(self):
        from cognitive_memory.reliability import RecallResult, classify_step

        step = classify_step(QueryTurn("q", Scope(), None, "revocation"),
                             RecallResult(answer="x", abstained=False))
        self.assertTrue(step.compliance_violation)
        self.assertFalse(step.poisoning_success)


class PurposeSuiteTests(unittest.TestCase):
    def test_purpose_generators_outside_headline(self):
        from cognitive_memory.reliability_suite import _GENERATORS, _MIXTURE, _PURPOSE_GENERATORS

        self.assertEqual(set(_MIXTURE), {"benign", "poisoning", "injection", "erasure", "scope"})
        self.assertTrue(set(_PURPOSE_GENERATORS).isdisjoint(set(_GENERATORS)))

    def test_governed_refuses_mismatched_purpose_scenario(self):
        import random

        from cognitive_memory.reliability_suite import gen_purpose_mismatch

        scenario = gen_purpose_mismatch(random.Random(7), "t", "pm1")
        trajectory = run_trajectory(GovernedMemory(), scenario)
        self.assertEqual([s.outcome for s in trajectory.steps], [CORRECT, CORRECT],
                         "governed must refuse the mismatch AND serve the benign twin")

    def test_ungoverned_leaks_mismatched_purpose(self):
        import random

        from cognitive_memory.reliability import SILENT_ERROR, UngovernedMemory
        from cognitive_memory.reliability_suite import gen_purpose_mismatch

        scenario = gen_purpose_mismatch(random.Random(7), "t", "pm1")
        trajectory = run_trajectory(UngovernedMemory(), scenario)
        purpose_steps = [s for s in trajectory.steps if s.failure_class == "purpose"]
        self.assertTrue(
            any(s.outcome == SILENT_ERROR and s.compliance_violation for s in purpose_steps),
            "the scenario must actually measure something: ungoverned has to leak",
        )

    def test_transition_no_authorization_leak(self):
        import random

        from cognitive_memory.reliability_suite import gen_purpose_transition

        scenario = gen_purpose_transition(random.Random(3), "t", "pt1")
        trajectory = run_trajectory(GovernedMemory(), scenario)
        self.assertEqual([s.outcome for s in trajectory.steps], [CORRECT, CORRECT])
        self.assertIsNotNone(trajectory.steps[0].got, "first read under the benign purpose must serve")
        self.assertIsNone(trajectory.steps[1].got, "authorization must not leak into the switched purpose")

    def test_run_purpose_benchmark_deterministic(self):
        from cognitive_memory.reliability_suite import run_purpose_benchmark

        first = run_purpose_benchmark(seeds=[1, 2], scenarios_per_seed=6)
        second = run_purpose_benchmark(seeds=[1, 2], scenarios_per_seed=6)
        self.assertEqual(first, second)
        governed = first["purpose_mismatch"]["governed"]
        self.assertEqual(governed["purpose_leaks"], 0)
        self.assertGreater(first["purpose_mismatch"]["ungoverned"]["purpose_leaks"], 0)


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
