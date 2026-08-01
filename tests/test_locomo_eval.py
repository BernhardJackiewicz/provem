import json
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "locomo", "fake_locomo.json")
MANIFEST = os.path.join(ROOT, "tests", "fixtures", "locomo", "manifest.json")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser, run_locomo_eval
from cognitive_memory.benchmark import BenchmarkRunner
from cognitive_memory.controller import MemoryController
from cognitive_memory.extractor import ExtractorSchemaError, GenericConversationExtractor, OpenConversationLLMExtractor
from cognitive_memory.locomo_eval import (
    CATEGORY_NAMES,
    LoCoMoEvaluationError,
    LoCoMoLoader,
    dumps_locomo_report,
    evaluate_locomo,
    locomo_path_from_manifest,
)
from cognitive_memory.models import Episode


class LoCoMoEvaluationTests(unittest.TestCase):
    def _llm_memory(
        self,
        dia_id,
        speaker,
        session_id,
        relation_type,
        subject,
        object_value,
        supporting_text,
        confidence=0.82,
        memory_type="semantic_fact",
        entity_mentions=None,
        resolved_entity=True,
        sensitivity="low",
    ):
        return {
            "provenance_dia_id": dia_id,
            "speaker": speaker,
            "session_id": session_id,
            "memory_type": memory_type,
            "relation_type": relation_type,
            "subject": subject,
            "object": object_value,
            "claim": "%s %s %s" % (subject, relation_type, object_value),
            "confidence": confidence,
            "temporal_hints": [],
            "entity_mentions": entity_mentions if entity_mentions is not None else [subject, object_value],
            "supporting_text": supporting_text,
            "sensitivity": sensitivity,
            "resolved_entity": resolved_entity,
        }

    def _write_temp_locomo(self, directory, payload, filename="fake_locomo_variant.json"):
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def _hybrid_retrieval_payload(self):
        return [
            {
                "sample_id": "hybrid_fake_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Maya", "dia_id": "D1:1", "text": "I moved to Oslo."},
                        {"speaker": "Maya", "dia_id": "D1:2", "text": "I love live music."},
                        {"speaker": "Maya", "dia_id": "D1:3", "text": "I play live chess."},
                        {"speaker": "Maya", "dia_id": "D1:4", "text": "I am live streaming."},
                        {"speaker": "Maya", "dia_id": "D1:5", "text": "I will live stream tomorrow."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "hybrid_q1",
                        "question": "Where does Maya live?",
                        "answer": "Oslo",
                        "category": 4,
                        "evidence": ["D1:1"],
                    }
                ],
            }
        ]

    def test_fake_locomo_fixture_loads(self):
        samples = LoCoMoLoader().load(FIXTURE)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_id, "fake_locomo_001")
        self.assertEqual(len(samples[0].questions), 3)

    def test_sessions_become_episodes(self):
        sample = LoCoMoLoader().load(FIXTURE)[0]

        self.assertEqual([episode.id for episode in sample.episodes], ["D1:1", "D1:2", "D2:1", "D2:2"])
        self.assertEqual(sample.episodes[0].project_id, "locomo")
        self.assertEqual(sample.episodes[0].context_id, "fake_locomo_001")
        self.assertLess(sample.episodes[0].timestamp, sample.episodes[-1].timestamp)

    def test_qa_annotations_and_evidence_parse(self):
        sample = LoCoMoLoader().load(FIXTURE)[0]
        temporal = [question for question in sample.questions if question.category_name == "temporal"][0]

        self.assertEqual(temporal.question_id, "fake_q3")
        self.assertEqual(temporal.answers, ["Friday"])
        self.assertEqual(temporal.evidence_ids, ["D2:1"])

    def test_category_names_match_official_locomo_mapping(self):
        self.assertEqual(
            CATEGORY_NAMES,
            {
                "1": "multi_hop",
                "2": "temporal",
                "3": "open_domain",
                "4": "single_hop",
                "5": "adversarial",
            },
        )

    def test_images_are_ignored_and_not_added_to_episode_text(self):
        sample = LoCoMoLoader().load(FIXTURE)[0]
        episode_text = "\n".join(episode.content for episode in sample.episodes)

        self.assertEqual(sample.ignored_image_count, 1)
        self.assertNotIn("https://example.invalid", episode_text)
        self.assertNotIn("synthetic image caption", episode_text.lower())

    def test_missing_dataset_path_fails_clearly(self):
        with self.assertRaises(LoCoMoEvaluationError) as context:
            LoCoMoLoader().load(os.path.join(ROOT, "data", "external", "locomo", "missing.json"))

        self.assertIn("does not exist", str(context.exception))
        self.assertIn("do not commit", str(context.exception))

    def test_manifest_resolves_local_fixture_path(self):
        path = locomo_path_from_manifest(MANIFEST)

        self.assertEqual(path, os.path.abspath(FIXTURE))

    def test_locomo_eval_runs_fake_fixture(self):
        report = evaluate_locomo(FIXTURE)

        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["qa_evaluated"], 3)
        self.assertEqual(report["ignored_image_count"], 1)
        self.assertEqual(report["summary"]["flat_lexical_rag"]["locomo_qa_accuracy"], 1.0)
        self.assertGreater(report["summary"]["long_context_latest"]["locomo_qa_accuracy"], 0.0)
        self.assertIn("cognitive_memory_layer", report["summary"])
        self.assertNotIn("stage_diagnostics", report)
        self.assertNotIn("stage_summary", report)

    def test_generic_extractor_extracts_speaker_grounded_facts(self):
        episode = Episode(
            "Maya: I drink green tea before work.",
            actor="Maya",
            source="chat",
            user_id="fake_locomo_001",
            project_id="locomo",
        )
        episode.id = "D1:1"

        candidates = GenericConversationExtractor().extract(episode)

        stored = [candidate for candidate in candidates if candidate.recommended_action == "store"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].metadata["subject"], "maya")
        self.assertEqual(stored[0].metadata["relation"], "habit")
        self.assertIn("green tea", stored[0].metadata["object"])
        self.assertEqual(stored[0].evidence_episode_ids, ["D1:1"])

    def test_generic_extractor_extracts_generic_open_conversation_relations(self):
        extractor = GenericConversationExtractor()
        examples = [
            ("Maya: I work as a nurse.", "maya", "career", "nurse"),
            ("Maya: I am dating Alex.", "maya", "relationship_status", "dating Alex"),
            ("Maya: My friend Alex lives in Paris.", "alex", "location", "Paris"),
            ("Maya: The support group is on Friday.", "maya", "support_group_time", "Friday"),
        ]

        for index, (text, subject, relation, object_text) in enumerate(examples, 1):
            episode = Episode(text, actor="Maya", source="chat", user_id="fake_locomo_001", project_id="locomo")
            episode.id = "D9:%d" % index
            stored = [
                candidate
                for candidate in extractor.extract(episode)
                if candidate.recommended_action == "store"
            ]

            self.assertTrue(stored, text)
            self.assertTrue(
                any(
                    candidate.metadata["subject"] == subject
                    and candidate.metadata["relation"] == relation
                    and object_text.lower() in candidate.metadata["object"].lower()
                    for candidate in stored
                ),
                text,
            )

    def test_generic_extractor_extracts_event_temporal_and_activity_coverage(self):
        extractor = GenericConversationExtractor()
        examples = [
            ("Melanie: I ran a charity race for mental health last Saturday.", "event", "charity race"),
            ("Melanie: I ran a charity race for mental health last Saturday.", "charity_race_time", "last Saturday"),
            ("Caroline: Researching adoption agencies - it's been a dream.", "researched_topic", "adoption agencies"),
            ("Melanie: I just signed up for a pottery class yesterday.", "activity", "pottery class"),
            ("Melanie: I just signed up for a pottery class yesterday.", "pottery_class_time", "yesterday"),
            ("Melanie: Yesterday I took the kids to the museum.", "visited_place", "museum"),
            ("Melanie: I just took my fam camping in the mountains last week.", "camping_location", "mountains"),
            ("Melanie: I've been running farther to de-stress, which has been great.", "destress_activity", "running farther"),
            ("Caroline: I talked about my transgender journey and encouraged students.", "identity_journey", "transgender"),
        ]

        for index, (text, relation, object_text) in enumerate(examples, 1):
            episode = Episode(text, actor=text.split(":", 1)[0], source="chat", user_id="fake_locomo_001", project_id="locomo")
            episode.id = "D8:%d" % index
            stored = [
                candidate
                for candidate in extractor.extract(episode)
                if candidate.recommended_action == "store"
            ]

            self.assertTrue(
                any(
                    candidate.metadata["relation"] == relation
                    and object_text.lower() in candidate.metadata["object"].lower()
                    for candidate in stored
                ),
                "%s -> %s" % (text, relation),
            )

    def test_generic_extractor_extracts_independent_location_and_event_classes(self):
        extractor = GenericConversationExtractor()
        examples = [
            ("Noah: I'm from Nairobi.", "origin_location", "Nairobi"),
            ("Noah: I grew up in Chicago.", "origin_location", "Chicago"),
            ("Noah: I was born in Lima.", "birth_location", "Lima"),
            ("Noah: We stayed near the lake during the retreat.", "stay_location", "lake"),
            ("Noah: Our cabin was near Pine Ridge.", "cabin_location", "Pine Ridge"),
            ("Noah: Our trip was to Kyoto.", "trip_location", "Kyoto"),
            ("Noah: The field station was near Quito.", "field_station_location", "Quito"),
            ("Noah: My mother lives near Porto.", "noah_mother", "location", "Porto"),
            ("Noah: My friend Riley moved to Denver.", "riley", "location", "Denver"),
            ("Riley: We visited the archive in Boston yesterday.", "event_summary", "visited the archive in Boston"),
        ]

        for index, item in enumerate(examples, 1):
            text = item[0]
            episode = Episode(text, actor=text.split(":", 1)[0], source="chat", user_id="independent_fake_001", project_id="open_eval")
            episode.id = "N1:%d" % index
            stored = [
                candidate
                for candidate in extractor.extract(episode)
                if candidate.recommended_action == "store"
            ]

            if len(item) == 3:
                _, relation, object_text = item
                self.assertTrue(
                    any(
                        candidate.metadata["relation"] == relation
                        and object_text.lower() in candidate.metadata["object"].lower()
                        for candidate in stored
                    ),
                    text,
                )
            else:
                _, subject, relation, object_text = item
                self.assertTrue(
                    any(
                        candidate.metadata["subject"] == subject
                        and candidate.metadata["relation"] == relation
                        and object_text.lower() in candidate.metadata["object"].lower()
                        for candidate in stored
                    ),
                    text,
                )

    def test_generic_extractor_location_negative_controls_do_not_store_locations(self):
        extractor = GenericConversationExtractor()
        examples = [
            "Noah: I left my notebook there.",
            "Noah: This place matters to me.",
            "Noah: I am from a place.",
            "Noah: The debate was in good faith.",
            "Noah: The plan is in motion.",
            "Noah: We should meet at some point.",
        ]

        for index, text in enumerate(examples, 1):
            episode = Episode(text, actor="Noah", source="chat", user_id="independent_fake_001", project_id="open_eval")
            episode.id = "NEG:%d" % index
            stored = [
                candidate
                for candidate in extractor.extract(episode)
                if candidate.recommended_action == "store"
            ]

            self.assertFalse(
                any("location" in candidate.metadata.get("relation", "") for candidate in stored),
                text,
            )

    def test_generic_extractor_ignores_uncertain_open_conversation_claims(self):
        episode = Episode(
            "Maya: Maybe I live in Paris.",
            actor="Maya",
            source="chat",
            user_id="fake_locomo_001",
            project_id="locomo",
        )
        episode.id = "D9:9"

        candidates = GenericConversationExtractor().extract(episode)

        self.assertEqual([candidate.recommended_action for candidate in candidates], ["ignore"])

    def test_generic_extracted_candidates_pass_through_controller(self):
        controller = MemoryController(extractor=GenericConversationExtractor())
        episode = Episode(
            "Liam: I live in Berlin near the canal.",
            actor="Liam",
            source="chat",
            user_id="fake_locomo_001",
            project_id="locomo",
        )
        episode.id = "D1:2"

        controller.ingest_episode(episode)
        facts = controller.store.list_facts(user_id="fake_locomo_001", project_id="locomo")

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].subject, "liam")
        self.assertEqual(facts[0].relation, "location")
        self.assertIn("D1:2", facts[0].evidence)

    def test_locomo_extraction_improves_fake_cml_without_default_change(self):
        default_report = evaluate_locomo(FIXTURE)
        extracted_report = evaluate_locomo(FIXTURE, extract=True, diagnostics=True)

        self.assertEqual(default_report["summary"]["cognitive_memory_layer"]["passed"], 0.0)
        self.assertEqual(extracted_report["summary"]["cognitive_memory_layer"]["passed"], 3.0)
        self.assertGreaterEqual(extracted_report["diagnostics"]["fake_locomo_001"]["accepted_facts"], 3)
        self.assertEqual(extracted_report["diagnostics"]["fake_locomo_001"]["retrieval_attempts"], 3)

    def test_evidence_recall_uses_dialog_ids(self):
        report = evaluate_locomo(FIXTURE)
        flat_scores = [score for score in report["scores"] if score["system"] == "flat_lexical_rag"]

        self.assertTrue(all(score["evidence_recall"] == 1.0 for score in flat_scores))

    def test_expected_answers_are_not_available_to_systems(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_secret.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"][0]["answer"] = "secret_expected_answer_not_in_dialog"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, limit_qa=1, extract=True)

        answers = " ".join(score["answer"] for score in report["scores"])
        self.assertNotIn("secret_expected_answer_not_in_dialog", answers)

    def test_qa_evidence_ids_are_not_used_for_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_secret_evidence.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"][0]["evidence"] = ["SECRET_QA_EVIDENCE_ONLY"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, extract=True, diagnostics=True)

        evidence_ids = report["diagnostics"]["fake_locomo_001"]["evidence_ids"]
        self.assertIn("D1:1", evidence_ids)
        self.assertNotIn("SECRET_QA_EVIDENCE_ONLY", evidence_ids)

    def test_qa_evidence_window_filter_keeps_only_questions_inside_turn_window(self):
        report = evaluate_locomo(
            FIXTURE,
            extract=True,
            stage_report=True,
            max_turns=2,
            qa_evidence_in_window_only=True,
        )

        self.assertEqual(report["qa_evaluated"], 2)
        self.assertEqual(report["qa_window_filter"]["qa_before_window_filter"], 3)
        self.assertEqual(report["qa_window_filter"]["qa_after_window_filter"], 2)
        self.assertEqual(report["qa_window_filter"]["qa_dropped_outside_window"], 1)
        question_ids = {
            item["question_id"]
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }
        self.assertEqual(question_ids, {"fake_q1", "fake_q2"})
        self.assertIn("fake_q3", report["qa_window_filter"]["dropped_question_ids"])

    def test_qa_evidence_window_filter_fails_when_empty(self):
        with self.assertRaises(LoCoMoEvaluationError) as context:
            evaluate_locomo(
                FIXTURE,
                extract=True,
                max_turns=0,
                qa_evidence_in_window_only=True,
            )

        self.assertIn("removed all QA items", str(context.exception))

    def test_qa_evidence_window_filter_does_not_leak_to_llm_extractor(self):
        seen_payloads = []

        def provider(source_turn):
            seen_payloads.append(dict(source_turn))
            return {"memories": []}

        with tempfile.TemporaryDirectory() as directory:
            evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=os.path.join(directory, "cache"),
                max_turns=2,
                max_api_calls=2,
                qa_evidence_in_window_only=True,
            )

        self.assertEqual([payload["dia_id"] for payload in seen_payloads], ["D1:1", "D1:2"])
        serialized = json.dumps(seen_payloads)
        self.assertNotIn("fake_q1", serialized)
        self.assertNotIn("fake_q2", serialized)
        self.assertNotIn("evidence", serialized.lower())
        self.assertNotIn("answer", serialized.lower())

    def test_stage_report_extraction_diagnostic_detects_evidence_memory(self):
        report = evaluate_locomo(FIXTURE, extract=True, stage_report=True)
        by_question = {
            item["question_id"]: item
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }

        diagnostic = by_question["fake_q1"]["extraction_diagnostic"]
        self.assertTrue(diagnostic["evidence_memory_exists"])
        self.assertEqual(diagnostic["covered_evidence_ids"], ["D1:1"])
        self.assertGreaterEqual(diagnostic["evidence_fact_count"], 1)
        self.assertEqual(report["stage_summary"]["cognitive_memory_layer"]["evidence_memory_recall"], 1.0)

    def test_stage_report_extraction_diagnostic_detects_missing_evidence_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_missing_evidence_memory.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"] = [
                {
                    "question_id": "missing_memory_q",
                    "question": "What helps Maya focus?",
                    "answer": "green tea",
                    "category": 4,
                    "evidence": ["D2:2"],
                }
            ]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, extract=True, stage_report=True)

        diagnostic = report["stage_diagnostics"]["cognitive_memory_layer"][0]["extraction_diagnostic"]
        self.assertFalse(diagnostic["evidence_memory_exists"])
        self.assertEqual(diagnostic["evidence_memory_count"], 0)
        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertIn("missing_extraction", stage["failure_labels"])
        self.assertGreater(report["stage_summary"]["cognitive_memory_layer"]["missing_extraction_rate"], 0.0)

    def test_stage_summary_reports_missing_extraction_cue_rates(self):
        payload = [
            {
                "sample_id": "cue_fake_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Maya", "dia_id": "D1:1", "text": "A nice place near Paris came up."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "cue_q",
                        "question": "Where is the nice place?",
                        "answer": "near Paris",
                        "category": 4,
                        "evidence": ["D1:1"],
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "cue_fake.json")

            report = evaluate_locomo(path, extract=True, stage_report=True)

        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertTrue(stage["extraction_diagnostic"]["missing_extraction_cues"]["location"])
        summary = report["stage_summary"]["cognitive_memory_layer"]
        self.assertEqual(summary["missing_extraction_with_location_cue_rate"], 1.0)

    def test_stage_summary_tracks_extraction_precision_against_qa_evidence(self):
        payload = [
            {
                "sample_id": "precision_fake_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Noah", "dia_id": "D1:1", "text": "I live in Lisbon."},
                        {"speaker": "Noah", "dia_id": "D1:2", "text": "I drink mint tea."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "precision_q",
                        "question": "Where does Noah live?",
                        "answer": "Lisbon",
                        "category": 4,
                        "evidence": ["D1:1"],
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "precision_fake.json")

            report = evaluate_locomo(path, extract=True, diagnostics=True, stage_report=True, retrieval_mode="hybrid")

        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertGreater(stage["extraction_diagnostic"]["sample_evidence_memory_precision"], 0.0)
        self.assertLess(stage["extraction_diagnostic"]["sample_evidence_memory_precision"], 1.0)
        self.assertIn("sample_evidence_memory_precision", report["stage_summary"]["cognitive_memory_layer"])

    def test_stage_report_retrieval_detects_missed_and_wrong_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_wrong_retrieval.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"] = [
                {
                    "question_id": "wrong_retrieval_q",
                    "question": "Where does Liam live?",
                    "answer": "green tea",
                    "category": 4,
                    "evidence": ["D1:1"],
                }
            ]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, extract=True, stage_report=True)

        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertTrue(stage["extraction_diagnostic"]["evidence_memory_exists"])
        self.assertFalse(stage["retrieval_diagnostic"]["top_k_evidence_hit"])
        self.assertTrue(stage["retrieval_diagnostic"]["selected_wrong_memory"])
        self.assertTrue(stage["abstention_diagnostic"]["wrong_memory_answer"])
        self.assertIn("retrieval_miss", stage["failure_labels"])

    def test_stage_report_retrieval_detects_top_k_evidence_hit(self):
        report = evaluate_locomo(FIXTURE, extract=True, stage_report=True)
        by_question = {
            item["question_id"]: item
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }

        diagnostic = by_question["fake_q2"]["retrieval_diagnostic"]
        self.assertTrue(diagnostic["top_k_evidence_hit"])
        self.assertEqual(diagnostic["selected_evidence_ids"], ["D1:2"])

    def test_stage_report_answer_diagnostic_direct_answer_and_format_mismatch(self):
        report = evaluate_locomo(FIXTURE, extract=True, stage_report=True)
        by_question = {
            item["question_id"]: item
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }

        diagnostic = by_question["fake_q1"]["answer_diagnostic"]
        self.assertEqual(diagnostic["synthesized_answer"], "green tea")
        self.assertGreaterEqual(diagnostic["synthesis_confidence"], 0.45)
        self.assertEqual(diagnostic["synthesis_question_type"], "object")
        self.assertTrue(diagnostic["synthesized_passed"])
        self.assertTrue(diagnostic["answer_format_mismatch"])

    def test_diagnostic_answer_mode_is_opt_in_and_answers_from_selected_memory(self):
        normal = evaluate_locomo(FIXTURE, extract=True, limit_qa=1, retrieval_mode="hybrid")
        diagnostic = evaluate_locomo(
            FIXTURE,
            extract=True,
            limit_qa=1,
            retrieval_mode="hybrid",
            answer_mode="diagnostic-synthesis",
            stage_report=True,
        )

        normal_score = [score for score in normal["scores"] if score["system"] == "cognitive_memory_layer"][0]
        diagnostic_score = [score for score in diagnostic["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertNotEqual(normal_score["answer"], diagnostic_score["answer"])
        self.assertEqual(diagnostic_score["answer"], "green tea")
        self.assertTrue(diagnostic_score["passed"])
        self.assertEqual(diagnostic_score["answer_mode"], "diagnostic-synthesis")
        self.assertEqual(diagnostic["answer_mode"], "diagnostic-synthesis")

    def test_diagnostic_answer_mode_does_not_use_expected_answer_label(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_secret_answer_diagnostic.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"] = [payload[0]["qa"][0]]
            payload[0]["qa"][0]["answer"] = "secret_expected_answer_not_in_dialog"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(
                path,
                extract=True,
                retrieval_mode="hybrid",
                answer_mode="diagnostic-synthesis",
            )

        score = [score for score in report["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertEqual(score["answer"], "green tea")
        self.assertNotIn("secret_expected_answer_not_in_dialog", score["answer"])
        self.assertFalse(score["passed"])

    def test_diagnostic_answer_mode_abstains_when_synthesis_is_not_direct(self):
        payload = self._hybrid_retrieval_payload()
        payload[0]["qa"][0]["question"] = "Is Maya moving to Oslo?"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "diagnostic_yes_no.json")

            report = evaluate_locomo(
                path,
                extract=True,
                retrieval_mode="hybrid",
                answer_mode="diagnostic-synthesis",
                stage_report=True,
            )

        score = [score for score in report["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertEqual(score["answer"], "ABSTAIN")
        self.assertEqual(score["abstain_reason"], "yes_no_requires_semantic_entailment")
        diagnostic = report["stage_diagnostics"]["cognitive_memory_layer"][0]["answer_diagnostic"]
        self.assertEqual(diagnostic["synthesis_question_type"], "yes_no")
        self.assertFalse(diagnostic["direct_answer_available"])

    def test_stage_report_answer_diagnostic_handles_temporal_and_relationship_templates(self):
        payload = [
            {
                "sample_id": "template_fake_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Maya", "dia_id": "D1:1", "text": "The support group is on Friday."},
                        {"speaker": "Maya", "dia_id": "D1:2", "text": "I am dating Alex."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "template_time_q",
                        "question": "When is Maya's support group?",
                        "answer": "Friday",
                        "category": 2,
                        "evidence": ["D1:1"],
                    },
                    {
                        "question_id": "template_relationship_q",
                        "question": "What is Maya's relationship status?",
                        "answer": "dating Alex",
                        "category": 4,
                        "evidence": ["D1:2"],
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "template_fake.json")

            report = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")

        by_question = {
            item["question_id"]: item
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }
        self.assertEqual(by_question["template_time_q"]["answer_diagnostic"]["synthesized_answer"].lower(), "friday")
        self.assertEqual(by_question["template_relationship_q"]["answer_diagnostic"]["synthesized_answer"], "dating Alex")
        self.assertGreater(report["stage_summary"]["cognitive_memory_layer"]["answer_synthesis_success_rate"], 0.0)

    def test_stage_report_answer_diagnostic_handles_location_event_and_who_templates(self):
        payload = [
            {
                "sample_id": "location_template_fake_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Noah", "dia_id": "D1:1", "text": "Yesterday I took the class to the observatory."},
                        {"speaker": "Noah", "dia_id": "D1:2", "text": "I just took my family camping near Cedar Lake last week."},
                        {"speaker": "Noah", "dia_id": "D1:3", "text": "I am dating Riley."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "where_visit_q",
                        "question": "Where did Noah take the class?",
                        "answer": "observatory",
                        "category": 4,
                        "evidence": ["D1:1"],
                    },
                    {
                        "question_id": "where_camping_q",
                        "question": "Where did Noah camp?",
                        "answer": "Cedar Lake",
                        "category": 4,
                        "evidence": ["D1:2"],
                    },
                    {
                        "question_id": "who_dating_q",
                        "question": "Who is Noah dating?",
                        "answer": "Riley",
                        "category": 4,
                        "evidence": ["D1:3"],
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "location_template_fake.json")

            report = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")

        by_question = {
            item["question_id"]: item
            for item in report["stage_diagnostics"]["cognitive_memory_layer"]
        }
        self.assertIn("observatory", by_question["where_visit_q"]["answer_diagnostic"]["synthesized_answer"].lower())
        self.assertIn("cedar lake", by_question["where_camping_q"]["answer_diagnostic"]["synthesized_answer"].lower())
        self.assertEqual(by_question["who_dating_q"]["answer_diagnostic"]["synthesized_answer"], "Riley")

    def test_stage_report_abstention_diagnostic_detects_unsafe_answer_without_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_secret_stage_evidence.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"] = [payload[0]["qa"][0]]
            payload[0]["qa"][0]["evidence"] = ["SECRET_QA_EVIDENCE_ONLY"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, extract=True, stage_report=True)

        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertFalse(stage["extraction_diagnostic"]["evidence_memory_exists"])
        self.assertTrue(stage["abstention_diagnostic"]["unsafe_answer"])

    def test_stage_report_abstention_diagnostic_detects_abstention_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake_locomo_abstain_with_evidence.json")
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"] = [
                {
                    "question_id": "abstain_with_evidence_q",
                    "question": "ZXQ galaxy nebula code?",
                    "answer": "green tea",
                    "category": 4,
                    "evidence": ["D1:1"],
                }
            ]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            report = evaluate_locomo(path, extract=True, stage_report=True)

        stage = report["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertTrue(stage["extraction_diagnostic"]["evidence_memory_exists"])
        self.assertTrue(stage["abstention_diagnostic"]["actual_abstain"])
        self.assertTrue(stage["abstention_diagnostic"]["abstention_with_evidence"])

    def test_hybrid_retrieval_improves_evidence_hit_on_open_conversation_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, self._hybrid_retrieval_payload())

            governed = evaluate_locomo(path, extract=True, stage_report=True)
            hybrid = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")

        governed_stage = governed["stage_diagnostics"]["cognitive_memory_layer"][0]
        hybrid_stage = hybrid["stage_diagnostics"]["cognitive_memory_layer"][0]
        self.assertFalse(governed_stage["retrieval_diagnostic"]["top_k_evidence_hit"])
        self.assertTrue(hybrid_stage["retrieval_diagnostic"]["top_k_evidence_hit"])
        self.assertFalse(hybrid_stage["abstention_diagnostic"]["actual_abstain"])
        self.assertGreater(hybrid_stage["retrieval_diagnostic"]["top_score"], 0.0)
        self.assertIn("score_margin", hybrid_stage["retrieval_diagnostic"])

    def test_hybrid_retrieval_does_not_use_evidence_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self._hybrid_retrieval_payload()
            path = self._write_temp_locomo(directory, payload, "hybrid_original.json")
            secret_payload = self._hybrid_retrieval_payload()
            secret_payload[0]["qa"][0]["evidence"] = ["SECRET_QA_EVIDENCE_ONLY"]
            secret_path = self._write_temp_locomo(directory, secret_payload, "hybrid_secret_evidence.json")

            original = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")
            secret = evaluate_locomo(secret_path, extract=True, stage_report=True, retrieval_mode="hybrid")

        original_score = [score for score in original["scores"] if score["system"] == "cognitive_memory_layer"][0]
        secret_score = [score for score in secret["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertEqual(original_score["answer"], secret_score["answer"])
        self.assertEqual(original_score["provenance"], secret_score["provenance"])

    def test_hybrid_retrieval_does_not_use_qa_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self._hybrid_retrieval_payload()
            path = self._write_temp_locomo(directory, payload, "hybrid_original_answer.json")
            secret_payload = self._hybrid_retrieval_payload()
            secret_payload[0]["qa"][0]["answer"] = "secret_expected_answer_not_in_dialog"
            secret_path = self._write_temp_locomo(directory, secret_payload, "hybrid_secret_answer.json")

            original = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")
            secret = evaluate_locomo(secret_path, extract=True, stage_report=True, retrieval_mode="hybrid")

        original_score = [score for score in original["scores"] if score["system"] == "cognitive_memory_layer"][0]
        secret_score = [score for score in secret["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertEqual(original_score["answer"], secret_score["answer"])
        self.assertNotIn("secret_expected_answer_not_in_dialog", secret_score["answer"])

    def test_hybrid_low_confidence_retrieval_abstains(self):
        payload = self._hybrid_retrieval_payload()
        payload[0]["qa"][0]["question"] = "What is Maya quantum token?"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "hybrid_low_confidence.json")

            report = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")

        score = [score for score in report["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertEqual(score["answer"], "ABSTAIN")
        self.assertTrue(score["actual_abstain"])
        self.assertEqual(score["abstain_reason"], "low_confidence_open_conversation")

    def test_hybrid_mode_does_not_change_governance_benchmark(self):
        report = BenchmarkRunner(suite="all").run()

        self.assertEqual(report["summary"]["cognitive_memory_layer"]["passed"], 216)

    def test_open_conversation_llm_extractor_accepts_valid_schema(self):
        episode = Episode(
            "Noah: My notebook is in the blue drawer.",
            actor="Noah",
            source="chat",
            user_id="llm_fake_001",
            project_id="open_eval",
        )
        episode.id = "L1:1"
        provider = lambda source_turn: {
            "memories": [
                self._llm_memory(
                    source_turn["dia_id"],
                    source_turn["speaker"],
                    source_turn["session_id"],
                    "object_location",
                    "notebook",
                    "blue drawer",
                    "My notebook is in the blue drawer",
                    entity_mentions=["notebook", "blue drawer"],
                )
            ]
        }
        controller = MemoryController(extractor=OpenConversationLLMExtractor(provider))

        candidates = controller.ingest_episode(episode)
        facts = controller.store.list_facts(user_id="llm_fake_001", project_id="open_eval")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].created_by, "llm")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].relation, "object_location")
        self.assertEqual(facts[0].object, "blue drawer")
        self.assertEqual(facts[0].evidence, ["L1:1"])

    def test_open_conversation_llm_extractor_rejects_invalid_json(self):
        episode = Episode("Noah: My notebook is in the blue drawer.", actor="Noah", source="chat")
        episode.id = "L1:1"
        extractor = OpenConversationLLMExtractor(lambda source_turn: "{not json")

        with self.assertRaises(ExtractorSchemaError):
            extractor.extract(episode)

    def test_open_conversation_llm_extractor_rejects_hallucinated_facts(self):
        episode = Episode(
            "Noah: My notebook is in the blue drawer.",
            actor="Noah",
            source="chat",
            user_id="llm_fake_001",
            project_id="open_eval",
        )
        episode.id = "L1:1"
        provider = lambda source_turn: {
            "memories": [
                self._llm_memory(
                    source_turn["dia_id"],
                    source_turn["speaker"],
                    source_turn["session_id"],
                    "object_location",
                    "notebook",
                    "Rome",
                    "My notebook is in the blue drawer",
                    entity_mentions=["notebook", "Rome"],
                )
            ]
        }
        controller = MemoryController(extractor=OpenConversationLLMExtractor(provider))

        candidates = controller.ingest_episode(episode)
        facts = controller.store.list_facts(user_id="llm_fake_001", project_id="open_eval")

        self.assertFalse(facts)
        self.assertEqual(candidates[0].recommended_action, "ignore")
        self.assertIn(candidates[0].metadata["llm_rejection_reason"], ("unsupported_memory", "hallucinated_entity"))

    def test_open_conversation_llm_extractor_rejects_missing_provenance(self):
        episode = Episode("Noah: My notebook is in the blue drawer.", actor="Noah", source="chat")
        episode.id = "L1:1"
        provider = lambda source_turn: {
            "memories": [
                self._llm_memory(
                    "WRONG",
                    source_turn["speaker"],
                    source_turn["session_id"],
                    "object_location",
                    "notebook",
                    "blue drawer",
                    "My notebook is in the blue drawer",
                    entity_mentions=["notebook", "blue drawer"],
                )
            ]
        }
        candidates = OpenConversationLLMExtractor(provider).extract(episode)

        self.assertEqual(candidates[0].recommended_action, "ignore")
        self.assertEqual(candidates[0].metadata["llm_rejection_reason"], "missing_or_wrong_provenance")

    def test_open_conversation_llm_extractor_ignores_ambiguous_references(self):
        episode = Episode("Noah: It is there.", actor="Noah", source="chat")
        episode.id = "L1:1"
        provider = lambda source_turn: {
            "memories": [
                self._llm_memory(
                    source_turn["dia_id"],
                    source_turn["speaker"],
                    source_turn["session_id"],
                    "ambiguous_reference",
                    "it",
                    "there",
                    "It is there",
                    entity_mentions=[],
                    resolved_entity=False,
                )
            ]
        }
        candidates = OpenConversationLLMExtractor(provider).extract(episode)

        self.assertEqual(candidates[0].recommended_action, "ignore")
        self.assertEqual(candidates[0].metadata["llm_rejection_reason"], "ambiguous_reference")

    def test_llm_extractor_does_not_receive_qa_answers_or_evidence_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(FIXTURE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload[0]["qa"][0]["answer"] = "secret_expected_answer_not_in_dialog"
            payload[0]["qa"][0]["evidence"] = ["SECRET_QA_EVIDENCE_ONLY"]
            path = self._write_temp_locomo(directory, payload, "llm_no_label_leakage.json")

            seen = []

            def provider(source_turn):
                self.assertEqual(set(source_turn), {"dia_id", "speaker", "session_id", "timestamp", "text"})
                self.assertNotIn("secret_expected_answer_not_in_dialog", json.dumps(source_turn))
                self.assertNotIn("SECRET_QA_EVIDENCE_ONLY", json.dumps(source_turn))
                seen.append(source_turn["dia_id"])
                if source_turn["dia_id"] == "D1:1":
                    return {
                        "memories": [
                            self._llm_memory(
                                "D1:1",
                                source_turn["speaker"],
                                source_turn["session_id"],
                                "question_answerable_fact",
                                "Maya",
                                "green tea",
                                "I drink green tea before work",
                                entity_mentions=["Maya", "green tea"],
                            )
                        ]
                    }
                return {"memories": []}

            report = evaluate_locomo(
                path,
                limit_qa=1,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=os.path.join(directory, "cache"),
                diagnostics=True,
                stage_report=True,
                retrieval_mode="hybrid",
            )

        cml_score = [score for score in report["scores"] if score["system"] == "cognitive_memory_layer"][0]
        self.assertIn("D1:1", seen)
        self.assertNotIn("secret_expected_answer_not_in_dialog", cml_score["answer"])
        self.assertEqual(report["diagnostics"]["fake_locomo_001"]["evidence_ids"], ["D1:1"])

    def test_mock_llm_fixture_improves_extraction_coverage_from_source_turns(self):
        payload = [
            {
                "sample_id": "llm_compare_001",
                "conversation": {
                    "session_1_date_time": "10:00 am on 1 January, 2026",
                    "session_1": [
                        {"speaker": "Noah", "dia_id": "D1:1", "text": "Paris is where the old map is stored."},
                    ],
                },
                "qa": [
                    {
                        "question_id": "map_q",
                        "question": "Where is the old map stored?",
                        "answer": "Paris",
                        "category": 4,
                        "evidence": ["D1:1"],
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_temp_locomo(directory, payload, "llm_compare.json")

            rule_based = evaluate_locomo(path, extract=True, stage_report=True, retrieval_mode="hybrid")

            def provider(source_turn):
                return {
                    "memories": [
                        self._llm_memory(
                            source_turn["dia_id"],
                            source_turn["speaker"],
                            source_turn["session_id"],
                            "object_location",
                            "old map",
                            "Paris",
                            "Paris is where the old map is stored",
                            entity_mentions=["old map", "Paris"],
                        )
                    ]
                }

            llm = evaluate_locomo(
                path,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=os.path.join(directory, "cache"),
                diagnostics=True,
                stage_report=True,
                retrieval_mode="hybrid",
            )

        rule_summary = rule_based["stage_summary"]["cognitive_memory_layer"]
        llm_summary = llm["stage_summary"]["cognitive_memory_layer"]
        self.assertEqual(rule_summary["evidence_memory_recall"], 0.0)
        self.assertGreater(llm_summary["evidence_memory_recall"], rule_summary["evidence_memory_recall"])
        self.assertEqual(llm_summary["sample_evidence_memory_precision"], 1.0)
        self.assertEqual(llm_summary["unsafe_answer_rate"], 0.0)
        self.assertEqual(llm["extractor_summary"]["llm_evidence_memory_recall"], 1.0)

    def test_llm_extraction_cache_miss_calls_provider_and_writes_cache(self):
        calls = []

        def provider(source_turn):
            calls.append(source_turn["dia_id"])
            return {
                "memories": [
                    self._llm_memory(
                        source_turn["dia_id"],
                        source_turn["speaker"],
                        source_turn["session_id"],
                        "question_answerable_fact",
                        source_turn["speaker"],
                        "green tea",
                        "I drink green tea before work",
                        entity_mentions=[source_turn["speaker"], "green tea"],
                    )
                ]
            }

        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=os.path.join(directory, "cache"),
                max_turns=1,
                max_api_calls=1,
                limit_qa=1,
                diagnostics=True,
                stage_report=True,
            )

        self.assertEqual(calls, ["D1:1"])
        self.assertEqual(report["llm_extraction"]["api_calls_made"], 1)
        self.assertEqual(report["llm_extraction"]["cache_misses"], 1)
        self.assertEqual(report["llm_extraction"]["cache_writes"], 1)

    def test_llm_extraction_cache_hit_avoids_provider_call(self):
        def provider(source_turn):
            return {
                "memories": [
                    self._llm_memory(
                        source_turn["dia_id"],
                        source_turn["speaker"],
                        source_turn["session_id"],
                        "question_answerable_fact",
                        source_turn["speaker"],
                        "green tea",
                        "I drink green tea before work",
                        entity_mentions=[source_turn["speaker"], "green tea"],
                    )
                ]
            }

        with tempfile.TemporaryDirectory() as directory:
            cache_dir = os.path.join(directory, "cache")
            evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=cache_dir,
                max_turns=1,
                max_api_calls=1,
                limit_qa=1,
            )

            def failing_provider(source_turn):
                raise AssertionError("provider should not be called on cache hit")

            report = evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_provider=failing_provider,
                llm_cache_dir=cache_dir,
                max_turns=1,
                max_api_calls=0,
                limit_qa=1,
                diagnostics=True,
                stage_report=True,
            )

        self.assertEqual(report["llm_extraction"]["cache_hits"], 1)
        self.assertEqual(report["llm_extraction"]["cache_misses"], 0)
        self.assertEqual(report["llm_extraction"]["api_calls_made"], 0)

    def test_llm_max_api_calls_blocks_before_provider_call(self):
        calls = []

        def provider(source_turn):
            calls.append(source_turn["dia_id"])
            return {"memories": []}

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(LoCoMoEvaluationError) as context:
                evaluate_locomo(
                    FIXTURE,
                    extract=True,
                    extractor_mode="llm",
                    llm_provider=provider,
                    llm_cache_dir=os.path.join(directory, "cache"),
                    max_turns=1,
                    max_api_calls=0,
                )

        self.assertEqual(calls, [])
        self.assertIn("exceeding --max-api-calls", str(context.exception))

    def test_llm_dry_run_cost_estimate_makes_zero_calls(self):
        calls = []

        def provider(source_turn):
            calls.append(source_turn["dia_id"])
            return {"memories": []}

        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_provider=provider,
                llm_cache_dir=os.path.join(directory, "cache"),
                max_turns=2,
                dry_run_cost_estimate=True,
            )

        self.assertEqual(calls, [])
        self.assertTrue(report["llm_extraction"]["dry_run"])
        self.assertEqual(report["llm_extraction"]["turns_selected"], 2)
        self.assertEqual(report["llm_extraction"]["api_calls_planned"], 2)
        self.assertEqual(report["llm_extraction"]["api_calls_made"], 0)
        self.assertEqual(report["scores"], [])

    def test_llm_sample_and_turn_bounds_shape_cost_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_locomo(
                FIXTURE,
                extract=True,
                extractor_mode="llm",
                llm_cache_dir=os.path.join(directory, "cache"),
                sample_ids=["fake_locomo_001"],
                max_samples=1,
                max_turns=2,
                dry_run_cost_estimate=True,
            )

        self.assertEqual(report["llm_extraction"]["sample_ids"], ["fake_locomo_001"])
        self.assertEqual(report["llm_extraction"]["samples_selected"], 1)
        self.assertEqual(report["llm_extraction"]["turns_selected"], 2)

    def test_llm_cache_path_is_ignored(self):
        with open(os.path.join(ROOT, ".gitignore"), "r", encoding="utf-8") as handle:
            ignore_rules = handle.read()

        self.assertIn(".cache/", ignore_rules)

    def test_llm_extractor_requires_extract_and_env_config_for_live_cli_mode(self):
        with self.assertRaises(LoCoMoEvaluationError):
            evaluate_locomo(FIXTURE, extractor_mode="llm")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LoCoMoEvaluationError) as context:
                evaluate_locomo(FIXTURE, extract=True, extractor_mode="llm", limit_qa=1, max_api_calls=10)

        self.assertIn("OPENAI_API_KEY", str(context.exception))

    def test_dumps_locomo_report(self):
        rendered = dumps_locomo_report(evaluate_locomo(FIXTURE))

        self.assertIn("LoCoMo evaluation", rendered)
        self.assertIn("text-only QA", rendered)
        self.assertIn("flat_lexical_rag", rendered)

    def test_cli_accepts_locomo_eval_path(self):
        args = build_parser().parse_args(
            [
                "locomo-eval",
                "--path",
                FIXTURE,
                "--json",
                "--extract",
                "--extractor",
                "llm",
                "--diagnostics",
                "--stage-report",
                "--retrieval-mode",
                "hybrid",
                "--max-samples",
                "1",
                "--max-turns",
                "2",
                "--max-api-calls",
                "3",
                "--sample-ids",
                "fake_locomo_001",
                "--llm-cache-dir",
                "/tmp/locomo-cache",
                "--qa-evidence-in-window-only",
                "--answer-mode",
                "diagnostic-synthesis",
            ]
        )

        self.assertEqual(args.command, "locomo-eval")
        self.assertEqual(args.path, FIXTURE)
        self.assertTrue(args.json)
        self.assertTrue(args.extract)
        self.assertEqual(args.extractor, "llm")
        self.assertTrue(args.diagnostics)
        self.assertTrue(args.stage_report)
        self.assertEqual(args.retrieval_mode, "hybrid")
        self.assertEqual(args.max_samples, 1)
        self.assertEqual(args.max_turns, 2)
        self.assertEqual(args.max_api_calls, 3)
        self.assertEqual(args.sample_ids, "fake_locomo_001")
        self.assertEqual(args.llm_cache_dir, "/tmp/locomo-cache")
        self.assertTrue(args.qa_evidence_in_window_only)
        self.assertEqual(args.answer_mode, "diagnostic-synthesis")

    def test_cli_passes_stage_report_to_evaluator(self):
        args = build_parser().parse_args(["locomo-eval", "--path", FIXTURE, "--stage-report", "--retrieval-mode", "hybrid"])
        with mock.patch("cognitive_memory.cli.evaluate_locomo") as evaluate:
            with mock.patch("cognitive_memory.cli.dumps_locomo_report", return_value="rendered"):
                evaluate.return_value = {"summary": {}, "scores": []}
                result = run_locomo_eval(args)

        self.assertEqual(result, 0)
        self.assertTrue(evaluate.call_args.kwargs["stage_report"])
        self.assertEqual(evaluate.call_args.kwargs["retrieval_mode"], "hybrid")
        self.assertEqual(evaluate.call_args.kwargs["extractor_mode"], "rule-based")
        self.assertEqual(evaluate.call_args.kwargs["sample_ids"], [])
        self.assertEqual(evaluate.call_args.kwargs["answer_mode"], "normal")
        self.assertFalse(evaluate.call_args.kwargs["qa_evidence_in_window_only"])

    def test_cli_passes_llm_extractor_to_evaluator(self):
        args = build_parser().parse_args(["locomo-eval", "--path", FIXTURE, "--extract", "--extractor", "llm"])
        with mock.patch("cognitive_memory.cli.evaluate_locomo") as evaluate:
            with mock.patch("cognitive_memory.cli.dumps_locomo_report", return_value="rendered"):
                evaluate.return_value = {"summary": {}, "scores": []}
                result = run_locomo_eval(args)

        self.assertEqual(result, 0)
        self.assertTrue(evaluate.call_args.kwargs["extract"])
        self.assertEqual(evaluate.call_args.kwargs["extractor_mode"], "llm")

    def test_cli_accepts_locomo_eval_manifest(self):
        args = build_parser().parse_args(["locomo-eval", "--manifest", MANIFEST])

        self.assertEqual(args.command, "locomo-eval")
        self.assertEqual(args.manifest, MANIFEST)
        self.assertEqual(args.retrieval_mode, "governed")

    def test_cli_missing_path_returns_setup_error(self):
        args = build_parser().parse_args(["locomo-eval", "--path", os.path.join(ROOT, "missing_locomo.json")])

        self.assertEqual(run_locomo_eval(args), 2)


if __name__ == "__main__":
    unittest.main()
