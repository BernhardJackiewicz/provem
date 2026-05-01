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

    def test_default_benchmark_still_passes_cognitive_layer(self):
        report = BenchmarkRunner().run()

        self.assertEqual(report["summary"]["cognitive_memory_layer"]["passed"], 34.0)
        self.assertEqual(report["summary"]["cognitive_memory_layer"]["total"], 34.0)


if __name__ == "__main__":
    unittest.main()
