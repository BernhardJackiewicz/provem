from __future__ import annotations

from typing import Optional

from ..models import Episode, MemoryCandidate, Reflection, RetrievalRequest, RetrievalResult, TemporalFact
from .base import AdapterConfigurationError, OptionalDependencyNotInstalled


class GraphitiBackend:
    """Stub for a future real Graphiti integration.

    This adapter is intentionally not wired. Use `MockGraphitiBackend` for
    tests until a real Graphiti/Neo4j contract test exists.
    """

    def __init__(self, neo4j_uri: Optional[str] = None, **_: object) -> None:
        try:
            import graphiti  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyNotInstalled(
                "GraphitiBackend requires the optional 'graphiti' extra. "
                "Install with `pip install -e .[graphiti]`, then add a real "
                "adapter implementation and service-level tests."
            ) from exc
        if not neo4j_uri:
            raise AdapterConfigurationError("GraphitiBackend requires neo4j_uri; real integration is not implemented yet.")

    def add_episode(self, episode: Episode) -> Episode:
        raise NotImplementedError("Real Graphiti episode ingestion is not implemented.")

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        raise NotImplementedError("Real Graphiti candidate storage is not implemented.")

    def add_fact(self, fact: TemporalFact) -> TemporalFact:
        raise NotImplementedError("Real Graphiti fact upsert is not implemented.")

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
