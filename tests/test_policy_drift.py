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


if __name__ == "__main__":
    unittest.main()
