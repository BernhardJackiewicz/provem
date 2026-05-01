import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.models import Episode, Reflection, RetrievalRequest
from cognitive_memory.persistence import load_snapshot, save_snapshot
from cognitive_memory.policy import PolicyStore
from cognitive_memory.retrieval import RetrievalPlanner
from cognitive_memory.store import InMemoryStore


def dt(day):
    return datetime(2026, 4, day, 10, 0, tzinfo=timezone.utc)


class ScopedReflectionTests(unittest.TestCase):
    def test_scoped_candidate_reflection_keeps_candidate_id_and_is_retrievable(self):
        store, policy, reflection = _store_with_reflection(
            claim="candidate_ana has recurring memory context around work_mode",
            actor_type="candidate",
            candidate_id="candidate_ana",
            subject_id="candidate_ana",
            relation_type="work_mode",
            reflection_type="candidate_preference",
        )

        result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="candidate ana recurring memory context work mode", task_type="reflection")
        )

        self.assertIn(reflection.id, [memory.id for memory in result.selected_memories])
        self.assertEqual(result.answer_text(), reflection.claim)
        self.assertEqual(reflection.candidate_id, "candidate_ana")
        self.assertCountEqual(result.provenance, reflection.supporting_evidence)

    def test_wrong_candidate_reflection_is_excluded_before_ranking(self):
        store, policy, reflection = _store_with_reflection(
            claim="candidate_ana has recurring memory context around work_mode",
            actor_type="candidate",
            candidate_id="candidate_ana",
            subject_id="candidate_ana",
            relation_type="work_mode",
            reflection_type="candidate_preference",
        )

        result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="candidate ben recurring memory context work mode", task_type="reflection")
        )

        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.id == reflection.id and item.reason == "wrong_scope" for item in result.excluded_memories))

    def test_role_specific_reflection_does_not_apply_to_other_role(self):
        store, policy, reflection = _store_with_reflection(
            claim="role_backend has recurring memory context around required_skill",
            actor_type="role",
            role_id="role_backend",
            subject_id="role_backend",
            relation_type="required_skill",
            reflection_type="role_requirement",
        )

        result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="role frontend recurring memory context required skill", task_type="reflection")
        )

        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.id == reflection.id and item.reason == "wrong_scope" for item in result.excluded_memories))

    def test_client_requirement_does_not_become_candidate_preference(self):
        store, policy, reflection = _store_with_reflection(
            claim="client_nova has recurring memory context around required_skill",
            actor_type="client",
            client_id="client_nova",
            subject_id="client_nova",
            relation_type="required_skill",
            reflection_type="client_requirement",
        )

        result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="candidate nova recurring memory context required skill", task_type="reflection")
        )

        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertTrue(any(item.id == reflection.id and item.reason == "wrong_scope" for item in result.excluded_memories))

    def test_same_company_employer_client_distinction_is_preserved(self):
        store = InMemoryStore()
        policy = PolicyStore()
        candidate_reflection = _add_reflection(
            store,
            "candidate_nova has recurring memory context around former_company",
            actor_type="candidate",
            candidate_id="candidate_nova",
            subject_id="candidate_nova",
            relation_type="former_company",
            reflection_type="candidate_preference",
        )
        client_reflection = _add_reflection(
            store,
            "client_nova has recurring memory context around required_skill",
            actor_type="client",
            client_id="client_nova",
            subject_id="client_nova",
            relation_type="required_skill",
            reflection_type="client_requirement",
        )

        candidate_result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="candidate nova recurring memory context former company", task_type="reflection")
        )
        client_result = RetrievalPlanner(store, policy).retrieve(
            RetrievalRequest(query="client nova recurring memory context required skill", task_type="reflection")
        )

        self.assertIn(candidate_reflection.id, [memory.id for memory in candidate_result.selected_memories])
        self.assertNotIn(client_reflection.id, [memory.id for memory in candidate_result.selected_memories])
        self.assertIn(client_reflection.id, [memory.id for memory in client_result.selected_memories])
        self.assertNotIn(candidate_reflection.id, [memory.id for memory in client_result.selected_memories])

    def test_persistence_roundtrip_preserves_reflection_scope(self):
        store, policy, reflection = _store_with_reflection(
            claim="candidate_ana has recurring memory context around work_mode",
            actor_type="candidate",
            candidate_id="candidate_ana",
            subject_id="candidate_ana",
            relation_type="work_mode",
            reflection_type="candidate_preference",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "memory.jsonl")
            save_snapshot(path, store, policy)
            snapshot = load_snapshot(path)

        loaded = snapshot.store.get_reflection(reflection.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.actor_type, "candidate")
        self.assertEqual(loaded.candidate_id, "candidate_ana")
        self.assertEqual(loaded.relation_type, "work_mode")
        self.assertEqual(loaded.scope_confidence, 0.95)
        self.assertEqual(loaded.reflection_type, "candidate_preference")


def _store_with_reflection(**kwargs):
    store = InMemoryStore()
    policy = PolicyStore()
    reflection = _add_reflection(store, **kwargs)
    return store, policy, reflection


def _add_reflection(
    store,
    claim,
    *,
    actor_type,
    candidate_id="",
    client_id="",
    role_id="",
    subject_id,
    relation_type,
    reflection_type,
):
    first = store.add_episode(Episode("fake evidence one", timestamp=dt(1)))
    second = store.add_episode(Episode("fake evidence two", timestamp=dt(2)))
    reflection = Reflection(
        claim=claim,
        confidence=0.75,
        supporting_evidence=[first.id, second.id],
        actor_type=actor_type,
        candidate_id=candidate_id,
        client_id=client_id,
        role_id=role_id,
        subject_id=subject_id,
        relation_type=relation_type,
        scope_confidence=0.95,
        reflection_type=reflection_type,
    )
    store.add_reflection(reflection)
    return reflection


if __name__ == "__main__":
    unittest.main()
