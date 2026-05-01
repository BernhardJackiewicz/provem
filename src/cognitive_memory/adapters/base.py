from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from ..models import Episode, MemoryCandidate, MemoryEvent, Reflection, RetrievalRequest, RetrievalResult, TemporalFact


class OptionalDependencyNotInstalled(ImportError):
    """Raised when an optional adapter dependency is not installed."""


class AdapterConfigurationError(RuntimeError):
    """Raised when an adapter is present but not configured enough to run."""


@runtime_checkable
class ExtractorPort(Protocol):
    """Port for deterministic or schema-constrained memory extraction."""

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        ...


@runtime_checkable
class LLMExtractorPort(Protocol):
    """Optional schema-constrained LLM extraction boundary.

    Implementations may propose memory candidates, but they must not write
    durable memory directly. The MemoryController remains the only write
    authority.
    """

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        ...


@runtime_checkable
class TemporalGraphBackend(Protocol):
    """Graphiti-like temporal fact storage/retrieval boundary.

    This is intentionally close to the current MVP 1 needs: storing temporal
    facts, preserving provenance, tracking supersession, and supporting local
    retrieval semantics. A future Graphiti adapter should implement this port
    without becoming a second write authority.
    """

    def add_episode(self, episode: Episode) -> Episode:
        ...

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        ...

    def add_fact(self, fact: TemporalFact) -> TemporalFact:
        ...

    def update_fact(self, fact: TemporalFact) -> TemporalFact:
        ...

    def get_fact(self, fact_id: str) -> Optional[TemporalFact]:
        ...

    def add_event(self, event: MemoryEvent) -> MemoryEvent:
        ...

    def update_event(self, event: MemoryEvent) -> MemoryEvent:
        ...

    def get_event(self, event_id: str) -> Optional[MemoryEvent]:
        ...

    def list_events(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[MemoryEvent]:
        ...

    def list_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        ...

    def active_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        ...

    def matching_active_facts(self, subject: str, relation: str, user_id: str, project_id: str) -> List[TemporalFact]:
        ...

    def add_reflection(self, reflection: Reflection) -> Reflection:
        ...

    def update_reflection(self, reflection: Reflection) -> Reflection:
        ...

    def list_reflections(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Reflection]:
        ...

    def audit(self, event: str, target_id: str) -> None:
        ...

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        ...


@runtime_checkable
class ExternalMemoryBackend(Protocol):
    """Mem0-like external memory extraction/retrieval baseline boundary."""

    def ingest(self, episode: Episode) -> None:
        ...

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        ...


@runtime_checkable
class StatefulAgentBackend(Protocol):
    """Letta-like stateful agent orchestration boundary."""

    def ingest(self, episode: Episode) -> None:
        ...

    def propose_memory(self, episode: Episode) -> List[MemoryCandidate]:
        ...

    def search_memory(self, request: RetrievalRequest) -> RetrievalResult:
        ...

    def write_procedure(self, reflection: Reflection) -> None:
        ...
