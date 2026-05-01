import json
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser, run_consolidation_eval
from cognitive_memory.consolidation_eval import consolidation_eval_scenarios, evaluate_consolidation
from cognitive_memory.controller import MemoryController
from cognitive_memory.reflection import SleepCycle


class ConsolidationEvalTests(unittest.TestCase):
    def test_cli_parser_accepts_consolidation_eval(self):
        args = build_parser().parse_args(["consolidation-eval"])

        self.assertEqual(args.command, "consolidation-eval")

    def test_report_contains_required_modes_and_metrics(self):
        report = evaluate_consolidation()
        summary = report["summary"]

        self.assertGreaterEqual(report["scenario_count"], 18)
        self.assertEqual(
            report["modes"],
            [
                "no_consolidation",
                "sleep_cycle_dry_run_only",
                "simulated_human_approved_consolidation",
            ],
        )
        for metric in (
            "consolidation_precision",
            "consolidation_recall",
            "unsafe_consolidation_rate",
            "scoped_consolidation_precision",
            "scoped_consolidation_recall",
            "cross_scope_reflection_leakage",
            "role_scope_leakage",
            "candidate_client_reflection_leakage",
            "overgeneralization_rate",
            "stale_fact_resurrection_rate",
            "review_required_accuracy",
            "downstream_task_delta",
            "provenance_coverage",
            "policy_violation_rate",
            "scope_leakage_rate",
        ):
            self.assertIn(metric, summary)

    def test_sleep_cycle_dry_run_mode_does_not_mutate_memory(self):
        scenario = _scenario("repeated_safe_user_pattern")
        controller = MemoryController()
        for episode in scenario.episodes:
            controller.ingest_episode(episode)

        before_reflections = list(controller.store.list_reflections())
        run = SleepCycle(controller.store, controller.policy).consolidate(project_id=scenario.project_id)
        after_reflections = list(controller.store.list_reflections())

        self.assertGreater(len(run.decisions), 0)
        self.assertEqual(before_reflections, after_reflections)

    def test_simulated_approval_only_changes_eval_copy(self):
        item = _report_item("repeated_safe_user_pattern")

        self.assertFalse(item["no_consolidation"]["passed"])
        self.assertFalse(item["sleep_cycle_dry_run_only"]["passed"])
        self.assertTrue(item["simulated_human_approved_consolidation"]["passed"])
        self.assertGreaterEqual(item["approved_count"], 1)

    def test_unsafe_or_unresolved_proposals_are_not_approved(self):
        report = evaluate_consolidation()
        unsafe_names = {
            "outdated_preference_requires_review",
            "historical_old_fact_not_current",
            "source_conflict_requires_review",
            "sensitive_evidence_requires_review",
            "prompt_injection_evidence_ignored",
            "deleted_and_do_not_use_evidence_ignored",
        }

        for item in report["scenarios"]:
            if item["name"] in unsafe_names:
                self.assertEqual(item["approved_count"], 0, item["name"])
                self.assertGreater(item["rejected_count"], 0, item["name"])
                self.assertEqual(item["unsafe_approved_count"], 0, item["name"])

    def test_repeated_safe_evidence_can_improve_downstream_retrieval(self):
        report = evaluate_consolidation()
        summary = report["summary"]

        self.assertGreater(summary["downstream_task_delta"], 0.0)
        self.assertEqual(summary["consolidation_recall"], 1.0)

    def test_conflicting_evidence_requires_review(self):
        item = _report_item("source_conflict_requires_review")

        self.assertTrue(item["review_required_ok"])
        self.assertEqual(item["approved_count"], 0)
        self.assertIn(
            "scope_has_review_required_conflict",
            {rejected["reason"] for rejected in item["rejected_decisions"]},
        )

    def test_provenance_and_policy_metrics_stay_clean(self):
        summary = evaluate_consolidation()["summary"]

        self.assertEqual(summary["provenance_coverage"], 1.0)
        self.assertEqual(summary["unsafe_consolidation_rate"], 0.0)
        self.assertEqual(summary["scoped_consolidation_precision"], 1.0)
        self.assertEqual(summary["scoped_consolidation_recall"], 1.0)
        self.assertEqual(summary["policy_violation_rate"], 0.0)
        self.assertEqual(summary["scope_leakage_rate"], 0.0)
        self.assertEqual(summary["cross_scope_reflection_leakage"], 0.0)
        self.assertEqual(summary["role_scope_leakage"], 0.0)
        self.assertEqual(summary["candidate_client_reflection_leakage"], 0.0)

    def test_scoped_candidate_client_consolidation_is_approved_without_leaking(self):
        item = _report_item("candidate_client_facts_do_not_merge")

        self.assertTrue(item["simulated_human_approved_consolidation"]["passed"])
        self.assertGreaterEqual(item["scoped_approved_count"], 1)
        self.assertFalse(item["candidate_client_reflection_leakage"])

    def test_role_scoped_background_approval_does_not_answer_wrong_role_query(self):
        item = _report_item("role_specific_pattern_does_not_leak")

        self.assertTrue(item["simulated_human_approved_consolidation"]["passed"])
        self.assertEqual(item["simulated_human_approved_consolidation"]["answer"], "ABSTAIN")
        self.assertFalse(item["role_scope_leakage"])

    def test_json_cli_output_is_machine_readable(self):
        args = build_parser().parse_args(["consolidation-eval", "--json"])
        with _CapturedStdout() as captured:
            code = run_consolidation_eval(args)

        self.assertEqual(code, 0)
        payload = json.loads(captured.output)
        self.assertIn("summary", payload)
        self.assertGreaterEqual(payload["scenario_count"], 18)


def _scenario(name):
    for scenario in consolidation_eval_scenarios():
        if scenario.name == name:
            return scenario
    raise AssertionError("Missing scenario: %s" % name)


def _report_item(name):
    for item in evaluate_consolidation()["scenarios"]:
        if item["name"] == name:
            return item
    raise AssertionError("Missing report item: %s" % name)


class _CapturedStdout:
    def __enter__(self):
        self._old = sys.stdout
        self._buffer = _Buffer()
        sys.stdout = self._buffer
        return self._buffer

    def __exit__(self, exc_type, exc, tb):
        sys.stdout = self._old


class _Buffer:
    def __init__(self):
        self.output = ""

    def write(self, text):
        self.output += text

    def flush(self):
        pass


if __name__ == "__main__":
    unittest.main()
