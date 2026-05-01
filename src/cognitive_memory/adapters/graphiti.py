from __future__ import annotations

import importlib
from typing import Dict, List, Optional

from ..models import Episode, MemoryCandidate, MemoryEvent, Reflection, RetrievalRequest, RetrievalResult, TemporalFact
from .local import LocalTemporalGraphBackend
from .base import AdapterConfigurationError, OptionalDependencyNotInstalled


class GraphitiBackend:
    """Stub for a future real Graphiti integration.

    This adapter is intentionally not wired. Use `MockGraphitiBackend` for
    tests until a real Graphiti/Neo4j contract test exists.
    """

    supports_temporal_facts = True
    supports_events = True
    supports_policy_metadata = True
    supports_provenance = True

    def __init__(self, neo4j_uri: Optional[str] = None, **_: object) -> None:
        if not _graphiti_module_available():
            raise OptionalDependencyNotInstalled(
                "GraphitiBackend requires the optional 'graphiti' extra. "
                "Install with `pip install -e .[graphiti]`, then add a real "
                "adapter implementation and service-level tests."
            )
        if not neo4j_uri:
            raise AdapterConfigurationError("GraphitiBackend requires neo4j_uri; real integration is not implemented yet.")

    def add_episode(self, episode: Episode) -> Episode:
        raise NotImplementedError("Real Graphiti episode ingestion is not implemented.")

    def write_episode(self, episode: Episode) -> Episode:
        raise NotImplementedError("Real Graphiti episode write is not implemented.")

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        raise NotImplementedError("Real Graphiti candidate storage is not implemented.")

    def add_fact(self, fact: TemporalFact) -> TemporalFact:
        raise NotImplementedError("Real Graphiti fact upsert is not implemented.")

    def write_temporal_fact(self, fact: TemporalFact) -> TemporalFact:
        raise NotImplementedError("Real Graphiti temporal fact write is not implemented.")

    def update_fact(self, fact: TemporalFact) -> TemporalFact:
        raise NotImplementedError("Real Graphiti fact update is not implemented.")

    def get_fact(self, fact_id: str) -> Optional[TemporalFact]:
        raise NotImplementedError("Real Graphiti fact lookup is not implemented.")

    def list_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None):
        raise NotImplementedError("Real Graphiti fact listing is not implemented.")

    def active_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None):
        raise NotImplementedError("Real Graphiti active fact listing is not implemented.")

    def matching_active_facts(self, subject: str, relation: str, user_id: str, project_id: str):
        raise NotImplementedError("Real Graphiti matching fact lookup is not implemented.")

    def add_event(self, event: MemoryEvent) -> MemoryEvent:
        raise NotImplementedError("Real Graphiti event storage is not implemented.")

    def write_memory_event(self, event: MemoryEvent) -> MemoryEvent:
        raise NotImplementedError("Real Graphiti memory event write is not implemented.")

    def update_event(self, event: MemoryEvent) -> MemoryEvent:
        raise NotImplementedError("Real Graphiti event update is not implemented.")

    def get_event(self, event_id: str) -> Optional[MemoryEvent]:
        raise NotImplementedError("Real Graphiti event lookup is not implemented.")

    def list_events(self, user_id: Optional[str] = None, project_id: Optional[str] = None):
        raise NotImplementedError("Real Graphiti event listing is not implemented.")

    def query_current_facts(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError("Real Graphiti current fact query is not implemented.")

    def query_historical_facts(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError("Real Graphiti historical fact query is not implemented.")

    def query_relationships(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError("Real Graphiti relationship query is not implemented.")

    def add_reflection(self, reflection: Reflection) -> Reflection:
        raise NotImplementedError("Real Graphiti reflection storage is not implemented.")

    def update_reflection(self, reflection: Reflection) -> Reflection:
        raise NotImplementedError("Real Graphiti reflection update is not implemented.")

    def list_reflections(self, user_id: Optional[str] = None, project_id: Optional[str] = None):
        raise NotImplementedError("Real Graphiti reflection listing is not implemented.")

    def audit(self, event: str, target_id: str) -> None:
        raise NotImplementedError("Real Graphiti audit integration is not implemented.")

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError("Real Graphiti search is not implemented.")


class LocalGraphitiParityBackend(LocalTemporalGraphBackend):
    """Dependency-free backend that exercises the Graphiti mapping contract.

    This is not Graphiti. It uses the same local store and policy gate as MVP 1
    so tests can validate mapping semantics before a real adapter exists.
    """

    name = "local_graphiti_parity"
    supports_temporal_facts = True
    supports_events = True
    supports_policy_metadata = True
    supports_provenance = True

    def capability_flags(self) -> Dict[str, bool]:
        return {
            "supports_temporal_facts": self.supports_temporal_facts,
            "supports_events": self.supports_events,
            "supports_policy_metadata": self.supports_policy_metadata,
            "supports_provenance": self.supports_provenance,
        }

    def write_episode(self, episode: Episode) -> Episode:
        return self.add_episode(episode)

    def write_temporal_fact(self, fact: TemporalFact) -> TemporalFact:
        return self.add_fact(fact)

    def write_memory_event(self, event: MemoryEvent) -> MemoryEvent:
        return self.add_event(event)

    def query_current_facts(self, request: RetrievalRequest) -> RetrievalResult:
        current_request = RetrievalRequest(
            query=request.query,
            user_id=request.user_id,
            project_id=request.project_id,
            task_type=request.task_type,
            time_scope="current",
            as_of=request.as_of,
            memory_policy=request.memory_policy,
            top_k=request.top_k,
        )
        return self.search(current_request)

    def query_historical_facts(self, request: RetrievalRequest) -> RetrievalResult:
        historical_request = RetrievalRequest(
            query=request.query,
            user_id=request.user_id,
            project_id=request.project_id,
            task_type=request.task_type,
            time_scope="as_of_date",
            as_of=request.as_of,
            memory_policy=request.memory_policy,
            top_k=request.top_k,
        )
        return self.search(historical_request)

    def query_relationships(self, request: RetrievalRequest) -> RetrievalResult:
        relationship_request = RetrievalRequest(
            query=request.query,
            user_id=request.user_id,
            project_id=request.project_id,
            task_type=request.task_type or "personalized",
            time_scope=request.time_scope,
            as_of=request.as_of,
            memory_policy=request.memory_policy,
            top_k=request.top_k,
        )
        return self.search(relationship_request)

    def mapped_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Dict[str, object]]:
        return [graphiti_fact_mapping(fact) for fact in self.list_facts(user_id=user_id, project_id=project_id)]

    def mapped_events(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Dict[str, object]]:
        return [graphiti_event_mapping(event) for event in self.list_events(user_id=user_id, project_id=project_id)]


def graphiti_fact_mapping(fact: TemporalFact) -> Dict[str, object]:
    return {
        "type": "temporal_fact_edge",
        "id": fact.id,
        "subject": fact.subject,
        "relation": fact.relation,
        "object": fact.object,
        "valid_at": fact.valid_at.isoformat(),
        "invalid_at": fact.invalid_at.isoformat() if fact.invalid_at else None,
        "confidence": fact.confidence,
        "provenance": list(fact.evidence),
        "supersedes": list(fact.supersedes),
        "superseded_by": fact.superseded_by,
        "policy": fact.privacy_policy,
        "source_trust": fact.source_trust,
        "source_type": fact.source_type,
        "scope": dict(fact.scope),
    }


def graphiti_event_mapping(event: MemoryEvent) -> Dict[str, object]:
    return {
        "type": "memory_event_node",
        "id": event.id,
        "event_type": event.event_type,
        "content": event.content,
        "timestamp": event.timestamp.isoformat(),
        "participants": [participant.to_dict() for participant in event.participants],
        "relations": [relation.to_dict() for relation in event.relations],
        "context": event.context.to_dict(),
        "provenance": list(event.evidence_episode_ids),
        "status": event.status,
    }


def _graphiti_module_available() -> bool:
    for module_name in ("graphiti", "graphiti_core"):
        try:
            importlib.import_module(module_name)
            return True
        except ImportError:
            continue
    return False
