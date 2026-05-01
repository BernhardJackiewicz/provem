import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "transcripts")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser
from cognitive_memory.models import RetrievalRequest
from cognitive_memory.persistence import load_snapshot, save_snapshot
from cognitive_memory.retrieval import RetrievalPlanner
from cognitive_memory.transcript_eval import (
    dumps_transcript_report,
    evaluate_transcript,
    evaluate_transcripts,
    load_transcripts,
    transcript_to_episodes,
)


class TranscriptEvaluationTests(unittest.TestCase):
    def test_transcript_fixtures_load(self):
        transcripts = load_transcripts(FIXTURES)

        self.assertGreaterEqual(len(transcripts), 10)
        self.assertTrue(any(transcript.domain == "recruiting" for transcript in transcripts))
        self.assertTrue(any(not transcript.expected_labels.has_labels() for transcript in transcripts))

    def test_transcript_turns_become_episodes(self):
        transcript = self._by_id("tx_recruiting_candidate_call")

        episodes = transcript_to_episodes(transcript)

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].context_id, transcript.transcript_id)
        self.assertEqual(episodes[0].project_id, "recruiting")
        self.assertEqual(episodes[0].actor, "candidate")
        self.assertEqual(episodes[0].source, "candidate")

    def test_expected_labels_parse(self):
        transcript = self._by_id("tx_recruiting_candidate_call")

        self.assertIn("candidate_ana salary_expectation 120k", transcript.expected_labels.facts_that_should_be_stored)
        self.assertEqual(transcript.expected_labels.identity_resolution_expected["crm_id"], "candidate_ana")

    def test_labeled_transcripts_produce_scores(self):
        report = evaluate_transcripts(FIXTURES)

        self.assertEqual(report["transcript_count"], 11)
        self.assertGreaterEqual(report["labeled_count"], 10)
        for metric in [
            "extraction_precision",
            "extraction_recall",
            "memory_write_precision",
            "memory_write_recall",
            "sensitive_storage_violation_rate",
            "identity_resolution_accuracy",
            "scope_accuracy",
            "current_truth_accuracy",
            "historical_truth_accuracy",
            "abstention_accuracy",
            "follow_up_action_safety",
            "provenance_coverage",
            "transcript_to_memory_latency",
        ]:
            self.assertIn(metric, report["summary"])

    def test_unlabeled_transcripts_produce_diagnostics(self):
        transcript = self._by_id("tx_generic_unlabeled_diagnostic")

        result = evaluate_transcript(transcript)

        self.assertFalse(result.labeled)
        self.assertIn("unlabeled_diagnostic_only", result.failures)
        self.assertIn("episodes_created", result.diagnostics)

    def test_sensitive_labels_are_redacted_in_summary(self):
        report = evaluate_transcripts(FIXTURES)

        rendered = dumps_transcript_report(report, as_json=True)

        self.assertNotIn("migraine", rendered.lower())
        self.assertNotIn("Zoe Fixture", rendered)

    def test_persistence_roundtrip_for_transcript_memories(self):
        transcript = self._by_id("tx_recruiting_salary_followup")
        result = evaluate_transcript(transcript)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "transcript.memory.jsonl")
            from cognitive_memory.controller import MemoryController
            from cognitive_memory.transcript_eval import _extractor_for_domain

            controller = MemoryController(extractor=_extractor_for_domain(transcript.domain))
            for episode in transcript_to_episodes(transcript):
                controller.ingest_episode(episode)
            save_snapshot(path, controller.store, controller.policy)
            snapshot = load_snapshot(path)

        after = RetrievalPlanner(snapshot.store, snapshot.policy).retrieve(
            RetrievalRequest(query="candidate ana salary expectation", user_id="candidate_ana", project_id="recruiting", task_type="temporal")
        )

        self.assertIn("140k", after.answer_text())
        self.assertNotIn("120k", after.answer_text())
        self.assertIn("current_truth_accuracy", result.metrics)

    def test_do_not_use_from_transcript_blocks_later_retrieval(self):
        transcript = self._by_id("tx_forget_sensitive_asr_noise")
        result = evaluate_transcript(transcript)

        self.assertEqual(result.metrics["do_not_use_leakage"], 0.0)
        checks = result.diagnostics["retrieval_checks"]
        former_company = [item for item in checks if item["query"] == "candidate zoe former company"][0]
        self.assertEqual(former_company["answer"], "ABSTAIN")

    def test_identity_ambiguity_causes_abstention(self):
        transcript = self._by_id("tx_ambiguous_identity")
        result = evaluate_transcript(transcript)

        self.assertEqual(result.metrics["identity_resolution_accuracy"], 1.0)
        self.assertEqual(result.metrics["abstention_accuracy"], 1.0)
        self.assertEqual(result.diagnostics["accepted_facts"], 0)

    def test_cli_accepts_transcript_eval_command(self):
        args = build_parser().parse_args(["transcript-eval", "--input", FIXTURES, "--json"])

        self.assertEqual(args.command, "transcript-eval")
        self.assertEqual(args.input, FIXTURES)
        self.assertTrue(args.json)

    def test_jsonl_transcripts_load(self):
        transcript = self._by_id("tx_recruiting_candidate_call")
        payload = {
            "transcript_id": transcript.transcript_id,
            "domain": transcript.domain,
            "timestamp": transcript.timestamp.isoformat(),
            "caller_identity": {
                "stated_name": transcript.caller_identity.stated_name,
                "crm_id": transcript.caller_identity.crm_id,
                "confidence": transcript.caller_identity.confidence,
            },
            "participants": [item.__dict__ for item in transcript.participants],
            "turns": [
                {
                    "speaker": item.speaker,
                    "text": item.text,
                    "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                    "asr_confidence": item.asr_confidence,
                }
                for item in transcript.turns
            ],
            "expected_labels": {
                "facts_that_should_be_stored": transcript.expected_labels.facts_that_should_be_stored,
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "one.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
            loaded = load_transcripts(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].transcript_id, transcript.transcript_id)

    def _by_id(self, transcript_id):
        for transcript in load_transcripts(FIXTURES):
            if transcript.transcript_id == transcript_id:
                return transcript
        raise AssertionError("missing transcript fixture %s" % transcript_id)


if __name__ == "__main__":
    unittest.main()
