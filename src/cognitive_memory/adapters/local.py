from __future__ import annotations

from typing import List, Optional

from ..models import Episode, MemoryCandidate, MemoryEvent, Reflection, RetrievalRequest, RetrievalResult, TemporalFact
from ..policy import PolicyStore
from ..retrieval import RetrievalPlanner
from ..store import InMemoryStore


class LocalTemporalGraphBackend:
    """Default dependency-free temporal backend.

    It wraps the current `InMemoryStore` so MVP 1 behavior remains unchanged
    while the controller depends on a backend contract instead of a concrete
    external graph service.
    """

    def __init__(self, store: Optional[InMemoryStore] = None, policy: Optional[PolicyStore] = None) -> None:
        self.store = store or InMemoryStore()
        self.policy = policy or PolicyStore()

    def add_episode(self, episode: Episode) -> Episode:
        return self.store.add_episode(episode)

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        return self.store.add_candidate(candidate)

    def add_fact(self, fact: TemporalFact) -> TemporalFact:
        return self.store.add_fact(fact)

    def update_fact(self, fact: TemporalFact) -> TemporalFact:
        return self.store.update_fact(fact)

    def get_fact(self, fact_id: str) -> Optional[TemporalFact]:
        return self.store.get_fact(fact_id)

    def add_event(self, event: MemoryEvent) -> MemoryEvent:
        return self.store.add_event(event)

    def update_event(self, event: MemoryEvent) -> MemoryEvent:
        return self.store.update_event(event)

    def get_event(self, event_id: str) -> Optional[MemoryEvent]:
        return self.store.get_event(event_id)

    def list_events(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[MemoryEvent]:
        return self.store.list_events(user_id=user_id, project_id=project_id)

    def list_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        return self.store.list_facts(user_id=user_id, project_id=project_id)

    def active_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        return self.store.active_facts(user_id=user_id, project_id=project_id)

    def matching_active_facts(self, subject: str, relation: str, user_id: str, project_id: str) -> List[TemporalFact]:
        return self.store.matching_active_facts(subject, relation, user_id, project_id)

    def add_reflection(self, reflection: Reflection) -> Reflection:
        return self.store.add_reflection(reflection)

    def update_reflection(self, reflection: Reflection) -> Reflection:
        return self.store.update_reflection(reflection)

    def list_reflections(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Reflection]:
        return self.store.list_reflections(user_id=user_id, project_id=project_id)

    def audit(self, event: str, target_id: str) -> None:
        self.store.audit(event, target_id)

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalPlanner(self.store, self.policy).retrieve(request)
