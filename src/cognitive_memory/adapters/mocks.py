from __future__ import annotations

from typing import List

from ..extractor import DeterministicExtractor
from ..models import (
    Episode,
    MemoryCandidate,
    Reflection,
    RetrievalRequest,
    RetrievalResult,
    SelectedMemory,
    lexical_score,
)
from ..retrieval import RetrievalPlanner
from .local import LocalTemporalGraphBackend


class MockGraphitiBackend(LocalTemporalGraphBackend):
    """In-memory mock for the Graphiti-like contract.

    This is not Graphiti. It only simulates the adapter boundary for tests.
    """

    name = "mock_graphiti"


class MockMem0Backend:
    """Deterministic in-memory mock for a Mem0-like external memory backend."""

    name = "mock_mem0"

    def __init__(self) -> None:
        self.episodes: List[Episode] = []

    def ingest(self, episode: Episode) -> None:
        self.episodes.append(episode)

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        scored = []
        for episode in self.episodes:
            if episode.user_id != request.user_id or episode.project_id != request.project_id:
                continue
            score = lexical_score(request.query, episode.content)
            if score > 0:
                scored.append((score, episode))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [
            SelectedMemory(
                id=episode.id,
                memory_type="mock_mem0_episode",
                claim=episode.content,
                score=score,
                confidence=0.5,
                evidence=[episode.id],
            )
            for score, episode in scored[: request.top_k]
        ]
        confidence = selected[0].confidence if selected else 0.0
        return RetrievalResult(
            selected_memories=selected,
            provenance=sorted({eid for memory in selected for eid in memory.evidence}),
            confidence=confidence,
            abstain_recommended=not selected,
            retrieval_trace="mock_mem0 selected=%s" % (",".join(memory.id for memory in selected) or "none"),
        )


class MockLettaBackend:
    """Deterministic in-memory mock for a Letta-like stateful agent backend."""

    name = "mock_letta"

    def __init__(self) -> None:
        self.extractor = DeterministicExtractor()
        self.graph = MockGraphitiBackend()
        self.procedure_writes: List[Reflection] = []
        self.tool_calls: List[str] = []

    def ingest(self, episode: Episode) -> None:
        self.graph.add_episode(episode)
        self.tool_calls.append("ingest")

    def propose_memory(self, episode: Episode) -> List[MemoryCandidate]:
        self.tool_calls.append("propose_memory")
        return self.extractor.extract(episode)

    def search_memory(self, request: RetrievalRequest) -> RetrievalResult:
        self.tool_calls.append("search_memory")
        return RetrievalPlanner(self.graph.store, self.graph.policy).retrieve(request)

    def write_procedure(self, reflection: Reflection) -> None:
        self.procedure_writes.append(reflection)
        self.tool_calls.append("write_procedure")
