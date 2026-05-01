import os
import sys
import unittest
from datetime import datetime, timezone


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.adapters.graphiti import (
    LocalGraphitiParityBackend,
    graphiti_event_mapping,
    graphiti_fact_mapping,
)
from cognitive_memory.adapters.local import LocalTemporalGraphBackend
from cognitive_memory.controller import MemoryController
from cognitive_memory.models import Episode, RetrievalRequest


def dt(day):
    return datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc)


class GraphitiParityTests(unittest.TestCase):
    def _controller(self, episodes):
        controller = MemoryController()
        for episode in episodes:
            controller.ingest_episode(episode)
        return controller

    def _backends(self, controller):
        return (
            LocalTemporalGraphBackend(store=controller.store, policy=controller.policy),
            LocalGraphitiParityBackend(store=controller.store, policy=controller.policy),
        )

    def _assert_result_equivalent(self, local_result, parity_result):
        self.assertEqual(parity_result.answer_text(), local_result.answer_text())
        self.assertEqual(parity_result.abstain_recommended, local_result.abstain_recommended)
        self.assertEqual(parity_result.abstain_reason, local_result.abstain_reason)
        self.assertEqual(parity_result.provenance, local_result.provenance)
        self.assertEqual(
            [(item.memory_type, item.claim) for item in parity_result.selected_memories],
            [(item.memory_type, item.claim) for item in local_result.selected_memories],
        )
        self.assertEqual(
            sorted((item.reason, item.memory_type, item.claim) for item in parity_result.excluded_memories),
            sorted((item.reason, item.memory_type, item.claim) for item in local_result.excluded_memories),
        )

    def _assert_search_parity(self, episodes, request, mode="search"):
        controller = self._controller(episodes)
        local, parity = self._backends(controller)
        local_result = local.search(request)
        if mode == "current":
            parity_result = parity.query_current_facts(request)
        elif mode == "historical":
            parity_result = parity.query_historical_facts(request)
        elif mode == "relationships":
            parity_result = parity.query_relationships(request)
        else:
            parity_result = parity.search(request)
        self._assert_result_equivalent(local_result, parity_result)
        return local_result, parity_result, parity

    def test_capability_flags_are_explicit(self):
        backend = LocalGraphitiParityBackend()

        self.assertEqual(
            backend.capability_flags(),
            {
                "supports_temporal_facts": True,
                "supports_events": True,
                "supports_policy_metadata": True,
                "supports_provenance": True,
            },
        )

    def test_current_truth_matches_local_backend(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT user|work_mode|remote", timestamp=dt(1)),
                Episode("FACT user|work_mode|hybrid", timestamp=dt(2)),
            ],
            RetrievalRequest(query="work mode", task_type="temporal"),
            mode="current",
        )

        self.assertIn("hybrid", local_result.answer_text())
        self.assertNotIn("remote", local_result.answer_text())

    def test_historical_truth_matches_local_backend(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT candidate_lee|notice_period|2_weeks", timestamp=dt(1)),
                Episode("FACT candidate_lee|notice_period|3_months", timestamp=dt(4)),
            ],
            RetrievalRequest(
                query="candidate lee notice period",
                task_type="temporal",
                time_scope="as_of_date",
                as_of=dt(2),
            ),
            mode="historical",
        )

        self.assertIn("2_weeks", local_result.answer_text())
        self.assertNotIn("3_months", local_result.answer_text())

    def test_superseded_fact_mapping_preserves_temporal_validity(self):
        _, _, parity = self._assert_search_parity(
            [
                Episode("FACT user|timezone|CET", timestamp=dt(1)),
                Episode("FACT user|timezone|EST", timestamp=dt(3)),
            ],
            RetrievalRequest(query="timezone", task_type="temporal"),
            mode="current",
        )

        mapped = {item["object"]: item for item in parity.mapped_facts()}
        self.assertIsNotNone(mapped["CET"]["invalid_at"])
        self.assertEqual(mapped["EST"]["invalid_at"], None)
        self.assertTrue(mapped["EST"]["supersedes"])

    def test_deleted_facts_are_excluded_in_parity_backend(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT user|blocked_company|Acme", timestamp=dt(1)),
                Episode("DELETE Acme", timestamp=dt(2)),
            ],
            RetrievalRequest(query="blocked company Acme", task_type="compliance"),
        )

        self.assertEqual(local_result.answer_text(), "ABSTAIN")
        self.assertEqual(local_result.abstain_reason, "forbidden_memory")

    def test_do_not_use_facts_are_excluded_in_parity_backend(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT candidate_tom|email|tom@example.com", timestamp=dt(1)),
                Episode("DO_NOT_USE tom@example.com", source="system_policy", timestamp=dt(2)),
            ],
            RetrievalRequest(query="candidate tom email", task_type="compliance"),
        )

        self.assertEqual(local_result.answer_text(), "ABSTAIN")
        self.assertEqual(local_result.abstain_reason, "forbidden_memory")

    def test_wrong_project_scope_is_excluded_before_ranking(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT user|tech_stack|Python", project_id="alpha", timestamp=dt(1)),
                Episode("FACT user|tech_stack|Rust", project_id="beta", timestamp=dt(1)),
            ],
            RetrievalRequest(query="tech stack", project_id="alpha", task_type="personalized"),
        )

        self.assertIn("Python", local_result.answer_text())
        self.assertNotIn("Rust", local_result.answer_text())
        self.assertTrue(any(item.reason == "wrong_project" for item in local_result.excluded_memories))

    def test_event_relationship_query_matches_local_backend(self):
        local_result, _, parity = self._assert_search_parity(
            [
                Episode("FACT candidate_sam|salary_expectation|120k", timestamp=dt(1)),
                Episode("FACT candidate_sam|target_client|Nova", timestamp=dt(1)),
                Episode("FACT candidate_sam|salary_expectation|150k", timestamp=dt(2)),
                Episode("FACT candidate_sam|target_client|Orion", timestamp=dt(2)),
            ],
            RetrievalRequest(query="candidate sam salary for client nova", task_type="personalized"),
            mode="relationships",
        )

        self.assertIn("120k", local_result.answer_text())
        self.assertNotIn("150k", local_result.answer_text())
        self.assertTrue(any(memory.memory_type == "memory_event" for memory in local_result.selected_memories))
        self.assertTrue(any(item["type"] == "memory_event_node" for item in parity.mapped_events()))

    def test_company_as_client_and_employer_mapping_stays_distinct(self):
        client_result, _, parity = self._assert_search_parity(
            [
                Episode("FACT client_acme|budget|180k", timestamp=dt(1)),
                Episode("FACT candidate_ana|former_company|Acme", timestamp=dt(1)),
            ],
            RetrievalRequest(query="client acme budget", task_type="personalized"),
            mode="relationships",
        )
        candidate_result = parity.query_relationships(
            RetrievalRequest(query="candidate ana former company", task_type="personalized")
        )

        self.assertIn("180k", client_result.answer_text())
        self.assertNotIn("former_company", client_result.answer_text())
        self.assertIn("Acme", candidate_result.answer_text())
        self.assertNotIn("180k", candidate_result.answer_text())
        mapped_events = parity.mapped_events()
        self.assertTrue(any("client_acme" in item["content"] for item in mapped_events))
        self.assertTrue(any("former_company" in item["content"] for item in mapped_events))

    def test_source_conflict_abstains_in_parity_backend(self):
        local_result, _, _ = self._assert_search_parity(
            [
                Episode("FACT client_nova|budget|130k", source="tool", timestamp=dt(1)),
                Episode("FACT client_nova|budget|300k", source="user", timestamp=dt(2)),
            ],
            RetrievalRequest(query="client nova budget", task_type="temporal"),
        )

        self.assertEqual(local_result.answer_text(), "ABSTAIN")
        self.assertEqual(local_result.abstain_reason, "source_conflict")

    def test_graphiti_mapping_preserves_provenance_and_policy_metadata(self):
        controller = self._controller([Episode("FACT user|timezone|CET", timestamp=dt(1))])
        fact = controller.store.list_facts()[0]
        event = controller.store.list_events()[0]

        mapped_fact = graphiti_fact_mapping(fact)
        mapped_event = graphiti_event_mapping(event)

        self.assertEqual(mapped_fact["provenance"], fact.evidence)
        self.assertEqual(mapped_fact["policy"], fact.privacy_policy)
        self.assertEqual(mapped_fact["source_trust"], fact.source_trust)
        self.assertEqual(mapped_fact["scope"]["project_id"], "default")
        self.assertEqual(mapped_event["provenance"], event.evidence_episode_ids)
        self.assertTrue(mapped_event["participants"])


if __name__ == "__main__":
    unittest.main()
