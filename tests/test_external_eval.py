import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "external")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser, run_external_eval
from cognitive_memory.external_eval import (
    ExternalDatasetConfig,
    ExternalValidationError,
    GenericTranscriptJsonLoader,
    GenericTranscriptJsonlLoader,
    PublicDatasetLoaderStub,
    dumps_external_report,
    evaluate_external_manifest,
    load_validation_manifest,
)


class ExternalValidationTests(unittest.TestCase):
    def test_manifest_parses_and_validates(self):
        manifest = load_validation_manifest(os.path.join(FIXTURES, "manifest.json"))

        self.assertEqual(len(manifest.datasets), 2)
        self.assertTrue(all(dataset.approved_for_eval for dataset in manifest.datasets))
        self.assertEqual(manifest.datasets[0].expected_schema, "generic_transcript_jsonl")

    def test_unapproved_dataset_refuses_to_run(self):
        with self.assertRaises(ExternalValidationError):
            load_validation_manifest(os.path.join(FIXTURES, "unapproved_manifest.json"))

    def test_missing_license_fails_safely(self):
        with self.assertRaises(ExternalValidationError):
            load_validation_manifest(os.path.join(FIXTURES, "missing_license_manifest.json"))

    def test_jsonl_loader_maps_external_records_to_transcripts(self):
        manifest = load_validation_manifest(os.path.join(FIXTURES, "manifest.json"))
        dataset = manifest.datasets[0]
        transcripts = GenericTranscriptJsonlLoader().load(os.path.join(FIXTURES, dataset.local_path), dataset)

        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0].transcript_id, "ext_recruiting_salary_fixture")
        self.assertEqual(transcripts[0].caller_identity.crm_id, "candidate_external")
        self.assertTrue(transcripts[0].expected_labels.has_labels())

    def test_json_loader_maps_generic_records_to_transcripts(self):
        manifest = load_validation_manifest(os.path.join(FIXTURES, "manifest.json"))
        dataset = manifest.datasets[1]
        transcripts = GenericTranscriptJsonLoader().load(os.path.join(FIXTURES, dataset.local_path), dataset)

        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0].transcript_id, "ext_generic_workmode_fixture")
        self.assertEqual(transcripts[0].participants[0].role, "customer")
        self.assertEqual(transcripts[0].turns[0].speaker, "customer")

    def test_public_dataset_loader_stub_fails_clearly(self):
        config = ExternalDatasetConfig(
            dataset_name="stub",
            source="placeholder",
            license="review required",
            pii_status="synthetic",
            local_path="unused.jsonl",
            approved_for_eval=True,
            expected_schema="public_dataset_stub",
        )

        with self.assertRaises(ExternalValidationError):
            PublicDatasetLoaderStub().load("unused.jsonl", config)

    def test_external_manifest_evaluates_approved_fake_data(self):
        report = evaluate_external_manifest(os.path.join(FIXTURES, "manifest.json"))

        self.assertEqual(report["dataset_count"], 2)
        self.assertEqual(report["evaluated_dataset_count"], 2)
        self.assertEqual(report["transcript_count"], 2)
        self.assertEqual(report["labeled_count"], 2)
        self.assertEqual(report["summary"]["current_truth_accuracy"], 1.0)
        self.assertEqual(report["summary"]["sensitive_storage_violation_rate"], 0.0)

    def test_external_report_redacts_sensitive_values(self):
        report = evaluate_external_manifest(os.path.join(FIXTURES, "manifest.json"))

        rendered = dumps_external_report(report, as_json=True)

        self.assertNotIn("fixture.person@example.invalid", rendered)
        self.assertNotIn("External Fixture Candidate", rendered)

    def test_cli_accepts_external_eval_command(self):
        args = build_parser().parse_args(["external-eval", "--manifest", os.path.join(FIXTURES, "manifest.json"), "--json"])

        self.assertEqual(args.command, "external-eval")
        self.assertTrue(args.json)

    def test_cli_refuses_unapproved_manifest(self):
        args = build_parser().parse_args(["external-eval", "--manifest", os.path.join(FIXTURES, "unapproved_manifest.json")])

        self.assertEqual(run_external_eval(args), 2)

    def test_real_external_dataset_paths_are_ignored(self):
        with open(os.path.join(ROOT, ".gitignore"), "r", encoding="utf-8") as handle:
            ignore = handle.read()

        self.assertIn("data/*", ignore)
        self.assertIn("transcripts/*", ignore)
        manifest = load_validation_manifest(os.path.join(FIXTURES, "manifest.json"))
        self.assertTrue(all(not dataset.local_path.startswith(("/", "~")) for dataset in manifest.datasets))


if __name__ == "__main__":
    unittest.main()
