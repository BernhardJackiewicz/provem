import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.adapters import (
    AdapterConfigurationError,
    ActiveStatePort,
    ExternalMemoryBackend,
    LLMExtractorPort,
    MockGraphitiBackend,
    MockLettaBackend,
    MockMem0Backend,
    OptionalDependencyNotInstalled,
    StatefulAgentBackend,
    TemporalGraphBackend,
    TemporalGraphPort,
)
from cognitive_memory.adapters.graphiti import GraphitiBackend
from cognitive_memory.adapters.letta import LettaBackend
from cognitive_memory.adapters.local import LocalTemporalGraphBackend
from cognitive_memory.adapters.mem0 import Mem0Backend
from cognitive_memory.benchmark import BenchmarkRunner, Scenario, all_scenarios
from cognitive_memory.cli import build_parser, run_benchmark
from cognitive_memory.controller import MemoryController
from cognitive_memory.extractor import SchemaConstrainedLLMExtractor
from cognitive_memory.mem0_audit import (
    categorize_mem0_failure,
    evaluate_mem0_audit,
    mem0_failure_diagnostics,
    mem0_simple_scenarios,
)
from cognitive_memory.models import Episode, RetrievalRequest
from cognitive_memory.retrieval import RetrievalPlanner


def dt(day):
    return datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc)


class FakeMem0Client:
    def __init__(self):
        self.add_calls = []
        self.memories = []

    def add(self, messages, user_id, metadata=None):
        self.add_calls.append({"messages": messages, "user_id": user_id, "metadata": metadata or {}})
        content = " ".join(message.get("content", "") for message in messages)
        self.memories.append(
            {
                "id": "fake_%s" % len(self.memories),
                "memory": content,
                "score": 0.9,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return {"id": self.memories[-1]["id"]}

    def search(self, query, filters=None, limit=10, **kwargs):
        user_id = (filters or {}).get("user_id") or kwargs.get("user_id")
        query_tokens = set(query.lower().replace("_", " ").split())
        results = []
        for memory in self.memories:
            if user_id and memory["user_id"] != user_id:
                continue
            memory_tokens = set(memory["memory"].lower().replace("_", " ").replace("|", " ").split())
            if query_tokens & memory_tokens:
                results.append(memory)
        return results[:limit]


class FakeMem0ClientWithAsyncMode(FakeMem0Client):
    def add(self, messages, user_id, metadata=None, async_mode=True):
        self.add_calls.append(
            {"messages": messages, "user_id": user_id, "metadata": metadata or {}, "async_mode": async_mode}
        )
        content = " ".join(message.get("content", "") for message in messages)
        self.memories.append(
            {
                "id": "fake_%s" % len(self.memories),
                "memory": content,
                "score": 0.9,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return {"id": self.memories[-1]["id"]}


class FakeMem0ClientWithPendingWrites(FakeMem0ClientWithAsyncMode):
    def add(self, messages, user_id, metadata=None, async_mode=True):
        super().add(messages, user_id, metadata=metadata, async_mode=async_mode)
        return {"status": "PENDING", "message": "Memory processing has been queued for background execution"}


class FakeMem0ClientWithDelete(FakeMem0Client):
    def __init__(self):
        super().__init__()
        self.delete_calls = []

    def delete_all(self, filters=None, user_id=None):
        self.delete_calls.append({"filters": filters or {}, "user_id": user_id})
        scoped_user_id = (filters or {}).get("user_id") or user_id
        self.memories = [memory for memory in self.memories if memory["user_id"] != scoped_user_id]
        return {"status": "ok"}


class FakeMem0ClientWithoutProvenance:
    def __init__(self):
        self.memories = []

    def add(self, messages, user_id, metadata=None):
        content = " ".join(message.get("content", "") for message in messages)
        self.memories.append({"id": "no_provenance_%s" % len(self.memories), "memory": content, "score": 0.7})

    def search(self, query, filters=None, limit=10, **kwargs):
        query_tokens = set(query.lower().replace("_", " ").split())
        results = []
        for memory in self.memories:
            memory_tokens = set(memory["memory"].lower().replace("_", " ").replace("|", " ").split())
            if query_tokens & memory_tokens:
                results.append(memory)
        return results[:limit]


class FakeMem0Memory(FakeMem0Client):
    def __init__(self, config=None, config_path=None):
        super().__init__()
        self.config = config
        self.config_path = config_path

    @classmethod
    def from_config(cls, config):
        return cls(config=config)

    @classmethod
    def from_config_file(cls, config_path):
        return cls(config_path=config_path)


class FakeMem0Module:
    Memory = FakeMem0Memory


class AdapterTests(unittest.TestCase):
    def test_mocks_conform_to_protocols(self):
        self.assertIsInstance(MockGraphitiBackend(), TemporalGraphBackend)
        self.assertIsInstance(MockMem0Backend(), ExternalMemoryBackend)
        self.assertIsInstance(MockLettaBackend(), StatefulAgentBackend)
        self.assertIsInstance(LocalTemporalGraphBackend(), TemporalGraphBackend)
        self.assertIsInstance(SchemaConstrainedLLMExtractor(lambda _: {"candidates": []}), LLMExtractorPort)
        self.assertIs(TemporalGraphPort, TemporalGraphBackend)
        self.assertIs(ActiveStatePort, StatefulAgentBackend)

    def test_mock_mem0_backend_searches_locally(self):
        backend = MockMem0Backend()
        episode = Episode("FACT user|work_mode|hybrid", timestamp=dt(1))

        backend.ingest(episode)
        result = backend.search(RetrievalRequest(query="work mode"))

        self.assertIn("hybrid", result.answer_text())
        self.assertEqual(result.provenance, [episode.id])

    def test_mock_letta_backend_proposes_memory_and_tracks_calls(self):
        backend = MockLettaBackend()
        episode = Episode("FACT user|work_mode|hybrid", timestamp=dt(1))

        backend.ingest(episode)
        candidates = backend.propose_memory(episode)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].metadata["relation"], "work_mode")
        self.assertIn("ingest", backend.tool_calls)
        self.assertIn("propose_memory", backend.tool_calls)

    def test_controller_can_use_mock_graphiti_backend_without_semantic_regression(self):
        backend = MockGraphitiBackend()
        controller = MemoryController(temporal_backend=backend)
        retrieval = RetrievalPlanner(controller.store, controller.policy)

        controller.ingest_episode(Episode("FACT user|work_mode|remote", timestamp=dt(1)))
        controller.ingest_episode(Episode("FACT user|work_mode|hybrid", timestamp=dt(2)))
        result = retrieval.retrieve(RetrievalRequest(query="work mode"))

        self.assertIn("hybrid", result.answer_text())
        self.assertNotIn("remote", result.answer_text())
        self.assertTrue(any(item.reason == "invalidated" for item in result.excluded_memories))
        self.assertIs(controller.temporal_backend, backend)

    def test_controller_mock_graphiti_preserves_policy_semantics(self):
        backend = MockGraphitiBackend()
        controller = MemoryController(temporal_backend=backend)
        retrieval = RetrievalPlanner(controller.store, controller.policy)

        controller.ingest_episode(Episode("FACT user|blocked_company|Acme", timestamp=dt(1)))
        controller.ingest_episode(Episode("DELETE Acme", timestamp=dt(2)))
        result = retrieval.retrieve(RetrievalRequest(query="blocked company Acme"))

        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.reason in ("deleted", "do_not_use", "deleted_evidence") for item in result.excluded_memories))

    def test_real_adapter_stubs_fail_clearly_without_optional_setup(self):
        cases = [
            (GraphitiBackend, ("graphiti", "graphiti_core"), "GraphitiBackend"),
            (LettaBackend, ("letta_client",), "LettaBackend"),
        ]
        for adapter_cls, package_names, adapter_name in cases:
            with self.subTest(adapter=adapter_name):
                if all(importlib.util.find_spec(package_name) is None for package_name in package_names):
                    with self.assertRaises(OptionalDependencyNotInstalled) as context:
                        adapter_cls()
                    self.assertIn(adapter_name, str(context.exception))
                else:
                    with self.assertRaises(AdapterConfigurationError) as context:
                        adapter_cls()
                    self.assertIn(adapter_name, str(context.exception))

    def test_mem0_backend_missing_dependency_behavior(self):
        with mock.patch("importlib.import_module", side_effect=ImportError("missing mem0")):
            with self.assertRaises(OptionalDependencyNotInstalled) as context:
                Mem0Backend()
        self.assertIn("Mem0Backend", str(context.exception))

    def test_mem0_backend_configuration_validation(self):
        with self.assertRaises(AdapterConfigurationError):
            Mem0Backend(client=FakeMem0Client(), api_key="not_allowed_with_client")

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AdapterConfigurationError):
                Mem0Backend(client_factory=lambda api_key: FakeMem0Client())

    def test_mem0_backend_with_injected_client_ingests_and_searches(self):
        client = FakeMem0Client()
        backend = Mem0Backend(client=client)
        episode = Episode("FACT user|work_mode|hybrid", timestamp=dt(1))

        backend.ingest(episode)
        result = backend.search(RetrievalRequest(query="work mode"))

        self.assertEqual(len(client.add_calls), 1)
        self.assertIn("hybrid", result.answer_text())
        self.assertEqual(result.provenance, [episode.id])

    def test_mem0_backend_requests_synchronous_add_when_supported(self):
        client = FakeMem0ClientWithAsyncMode()
        backend = Mem0Backend(client=client)

        backend.ingest(Episode("FACT user|work_mode|hybrid", timestamp=dt(1)))

        self.assertEqual(client.add_calls[0]["async_mode"], False)

    def test_mem0_backend_settles_pending_writes_before_search(self):
        client = FakeMem0ClientWithPendingWrites()
        backend = Mem0Backend(client=client, write_settle_seconds=1.5)

        backend.ingest(Episode("FACT user|drink_preference|coffee", timestamp=dt(1)))
        with mock.patch("cognitive_memory.adapters.mem0.time.sleep") as sleep:
            result = backend.search(RetrievalRequest(query="drink preference coffee"))

        sleep.assert_called_once_with(1.5)
        self.assertIn("coffee", result.answer_text())

    def test_mem0_backend_oss_mode_uses_memory_from_config(self):
        with mock.patch("cognitive_memory.adapters.mem0.importlib.import_module", return_value=FakeMem0Module):
            backend = Mem0Backend(mode="oss", oss_config={"llm": {"provider": "ollama"}})

        episode = Episode("FACT user|work_mode|hybrid", timestamp=dt(1))
        backend.ingest(episode)
        result = backend.search(RetrievalRequest(query="work mode"))

        self.assertEqual(backend.mode, "oss")
        self.assertEqual(backend.client.config, {"llm": {"provider": "ollama"}})
        self.assertIn("hybrid", result.answer_text())
        self.assertEqual(result.metadata["mode"], "oss")

    def test_mem0_backend_oss_mode_can_use_config_path_without_api_key(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"embedder": {"provider": "ollama"}}, handle)
            config_path = handle.name
        try:
            with mock.patch("cognitive_memory.adapters.mem0.importlib.import_module", return_value=FakeMem0Module):
                backend = Mem0Backend(mode="oss", oss_config_path=config_path)
        finally:
            os.unlink(config_path)

        self.assertEqual(backend.mode, "oss")
        self.assertEqual(backend.client.config_path, config_path)

    def test_mem0_backend_oss_mode_requires_config(self):
        with mock.patch("cognitive_memory.adapters.mem0.importlib.import_module", return_value=FakeMem0Module):
            with self.assertRaises(AdapterConfigurationError):
                Mem0Backend(mode="oss")

    def test_mem0_backend_filters_project_metadata(self):
        client = FakeMem0Client()
        backend = Mem0Backend(client=client)
        backend.ingest(Episode("FACT user|tech_stack|Python", timestamp=dt(1), project_id="alpha"))
        backend.ingest(Episode("FACT user|tech_stack|Rust", timestamp=dt(2), project_id="beta"))

        result = backend.search(RetrievalRequest(query="tech stack", project_id="alpha"))

        self.assertIn("Python", result.answer_text())
        self.assertNotIn("Rust", result.answer_text())

    def test_benchmark_mem0_skips_when_unavailable(self):
        with mock.patch("cognitive_memory.adapters.mem0.importlib.import_module", side_effect=ImportError("missing mem0")):
            report = BenchmarkRunner(include_mem0=True).run()

        self.assertIn("mem0_external", report["skipped_optional"])
        self.assertNotIn("mem0_external", report["summary"])

    def test_benchmark_mem0_strict_optional_fails_clearly(self):
        args = build_parser().parse_args(["benchmark", "--include-mem0", "--strict-optional"])

        with mock.patch("cognitive_memory.adapters.mem0.importlib.import_module", side_effect=ImportError("missing mem0")):
            self.assertEqual(run_benchmark(args), 2)

    def test_quality_gate_does_not_enable_mem0_by_default(self):
        args = build_parser().parse_args(["quality-gate"])

        self.assertEqual(args.command, "quality-gate")
        self.assertFalse(hasattr(args, "include_mem0"))

    def test_benchmark_mem0_with_fake_backend(self):
        scenario = Scenario(
            name="mem0_fake_baseline",
            category="current_fact",
            episodes=[Episode("FACT user|work_mode|hybrid", timestamp=dt(1))],
            query="work mode",
            expected_include=["hybrid"],
        )

        report = BenchmarkRunner(
            scenarios=[scenario],
            include_mem0=True,
            mem0_backend_factory=lambda: Mem0Backend(client=FakeMem0Client()),
        ).run()

        self.assertIn("mem0_external", report["summary"])
        self.assertEqual(report["summary"]["mem0_external"]["passed"], 1.0)
        mem0_score = [score for score in report["scores"] if score["system"] == "mem0_external"][0]
        self.assertTrue(mem0_score["selected_memories"])
        self.assertTrue(mem0_score["normalized_fields"]["selected_memories_available"])
        self.assertTrue(mem0_score["normalized_fields"]["provenance_available"])
        self.assertFalse(mem0_score["normalized_fields"]["abstention_available"])

    def test_mem0_result_normalization_marks_unavailable_fields(self):
        scenario = Scenario(
            name="mem0_no_provenance",
            category="current_fact",
            episodes=[Episode("FACT user|work_mode|hybrid", timestamp=dt(1))],
            query="work mode",
            expected_include=["hybrid"],
        )

        report = BenchmarkRunner(
            scenarios=[scenario],
            include_mem0=True,
            mem0_backend_factory=lambda: Mem0Backend(client=FakeMem0ClientWithoutProvenance()),
        ).run()
        mem0_score = [score for score in report["scores"] if score["system"] == "mem0_external"][0]

        self.assertEqual(mem0_score["provenance"], [])
        self.assertTrue(mem0_score["normalized_fields"]["selected_memories_available"])
        self.assertFalse(mem0_score["normalized_fields"]["provenance_available"])
        self.assertFalse(mem0_score["normalized_fields"]["abstention_available"])
        self.assertIsNone(report["summary"]["mem0_external"]["provenance_coverage"])
        self.assertEqual(report["summary"]["mem0_external"]["provenance_available_rate"], 0.0)

    def test_mem0_fake_local_fixture_uses_existing_scenarios(self):
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "mem0", "fake_mem0_suite.json")
        with open(fixture_path, "r", encoding="utf-8") as handle:
            fixture = json.load(handle)
        scenarios_by_name = {scenario.name: scenario for scenario in all_scenarios()}
        scenarios = [scenarios_by_name[name] for name in fixture["scenario_names"]]

        report = BenchmarkRunner(
            scenarios=scenarios,
            include_mem0=True,
            mem0_backend_factory=lambda: Mem0Backend(client=FakeMem0Client()),
        ).run()
        mem0_scores = [score for score in report["scores"] if score["system"] == "mem0_external"]

        self.assertEqual(len(mem0_scores), len(fixture["scenario_names"]))
        self.assertNotIn("expected_include", json.dumps(mem0_scores))
        self.assertTrue(all("normalized_fields" in score for score in mem0_scores))

    def test_mem0_baseline_does_not_read_expected_outputs(self):
        hidden_expected = "hidden_expected_value_that_never_appears"
        scenario = Scenario(
            name="mem0_anti_cheat_hidden_expected",
            category="abstention",
            episodes=[Episode("FACT user|work_mode|hybrid", timestamp=dt(1))],
            query="work mode",
            expected_include=[hidden_expected],
        )

        report = BenchmarkRunner(
            scenarios=[scenario],
            include_mem0=True,
            mem0_backend_factory=lambda: Mem0Backend(client=FakeMem0Client()),
        ).run()
        mem0_score = [score for score in report["scores"] if score["system"] == "mem0_external"][0]

        self.assertNotIn(hidden_expected, mem0_score["answer"])
        self.assertFalse(mem0_score["passed"])

    def test_mem0_baseline_uses_synthetic_namespaces_per_scenario(self):
        clients = []

        def factory():
            client = FakeMem0Client()
            clients.append(client)
            return Mem0Backend(client=client)

        scenarios = [
            Scenario(
                name="mem0_namespace_one",
                category="current_fact",
                episodes=[Episode("FACT user|work_mode|hybrid", timestamp=dt(1))],
                query="work mode",
                expected_include=["hybrid"],
            ),
            Scenario(
                name="mem0_namespace_two",
                category="current_fact",
                episodes=[Episode("FACT user|work_mode|remote", timestamp=dt(2))],
                query="work mode",
                expected_include=["remote"],
            ),
        ]

        BenchmarkRunner(
            scenarios=scenarios,
            suite="structured",
            include_mem0=True,
            mem0_backend_factory=factory,
        ).run()

        user_ids = [
            call["user_id"]
            for client in clients
            for call in client.add_calls
        ]
        self.assertEqual(len(user_ids), 2)
        self.assertTrue(all(user_id.startswith("engram_mem0_structured_") for user_id in user_ids))
        self.assertNotEqual(user_ids[0], user_ids[1])
        self.assertTrue(all(user_id.endswith(":user") for user_id in user_ids))

    def test_mem0_baseline_cleans_up_synthetic_namespace_when_supported(self):
        clients = []

        def factory():
            client = FakeMem0ClientWithDelete()
            clients.append(client)
            return Mem0Backend(client=client)

        scenario = Scenario(
            name="mem0_cleanup",
            category="current_fact",
            episodes=[Episode("FACT user|work_mode|hybrid", timestamp=dt(1))],
            query="work mode",
            expected_include=["hybrid"],
        )

        BenchmarkRunner(
            scenarios=[scenario],
            suite="structured",
            include_mem0=True,
            mem0_backend_factory=factory,
        ).run()

        self.assertEqual(len(clients), 1)
        self.assertEqual(len(clients[0].delete_calls), 1)
        deleted_user = clients[0].delete_calls[0]["user_id"] or clients[0].delete_calls[0]["filters"]["user_id"]
        self.assertTrue(deleted_user.startswith("engram_mem0_structured_"))
        self.assertTrue(deleted_user.endswith(":user"))

    def test_mem0_simple_sanity_suite_definition(self):
        scenarios = mem0_simple_scenarios()

        self.assertGreaterEqual(len(scenarios), 10)
        self.assertTrue(all(scenario.suite == "mem0_sanity" for scenario in scenarios))
        self.assertTrue(any(scenario.category == "simple_memory" for scenario in scenarios))
        self.assertTrue(any(scenario.category == "simple_historical_memory" for scenario in scenarios))

    def test_mem0_audit_with_fake_client_reports_diagnostics_and_metrics(self):
        report = evaluate_mem0_audit(mem0_backend_factory=lambda: Mem0Backend(client=FakeMem0Client()))

        self.assertEqual(report["status"], "complete")
        self.assertIn("simple_memory_accuracy", report["summary"])
        self.assertIn("mem0_retrieval_success_rate", report["summary"])
        self.assertIn("fairness_audit", report)
        self.assertFalse(report["fairness_audit"]["expected_outputs_visible_to_mem0"])

    def test_mem0_diagnostics_format_and_failure_category(self):
        score = {
            "scenario": "diagnostic_case",
            "category": "current_fact",
            "passed": False,
            "answer": "ABSTAIN",
            "selected_memories": [],
            "normalized_fields": {"abstention_available": False},
        }

        diagnostics = mem0_failure_diagnostics([score])

        self.assertEqual(diagnostics[0]["scenario_id"], "diagnostic_case")
        self.assertEqual(diagnostics[0]["failure_category"], "no_retrieval")
        self.assertIn("native_abstention", diagnostics[0]["unavailable_fields"])

    def test_mem0_failure_categorization_keeps_unavailable_fields_separate(self):
        score = {
            "scenario": "policy_case",
            "category": "do_not_use",
            "passed": False,
            "answer": "FACT user|avoid_company|Globex",
            "selected_memories": [{"id": "m1"}],
            "normalized_fields": {"selected_memories_available": True, "provenance_available": False},
            "do_not_use_leak": True,
        }

        self.assertEqual(categorize_mem0_failure(score), "policy_missing")

    def test_cli_accepts_mem0_sanity_command(self):
        args = build_parser().parse_args(["mem0-sanity", "--strict-optional", "--governance-suite", "structured"])

        self.assertEqual(args.command, "mem0-sanity")
        self.assertTrue(args.strict_optional)
        self.assertEqual(args.governance_suite, "structured")

    def test_default_benchmark_still_passes_cognitive_layer(self):
        report = BenchmarkRunner().run()

        self.assertEqual(report["summary"]["cognitive_memory_layer"]["passed"], 34.0)
        self.assertEqual(report["summary"]["cognitive_memory_layer"]["total"], 34.0)


if __name__ == "__main__":
    unittest.main()
