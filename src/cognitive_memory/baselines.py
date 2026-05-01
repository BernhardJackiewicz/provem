from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .adapters.base import ExternalMemoryBackend
from .extractor import DeterministicExtractor
from .models import Episode, RetrievalRequest, TemporalFact, clamp, lexical_score
from .store import InMemoryStore


class BaselineResult:
    def __init__(
        self,
        answer: str,
        trace: str,
        provenance: Optional[List[str]] = None,
        abstain_reason: str = "",
        selected_memories: Optional[List[Dict[str, object]]] = None,
        normalized_fields: Optional[Dict[str, object]] = None,
    ) -> None:
        self.answer = answer
        self.trace = trace
        self.provenance = provenance or []
        self.abstain_reason = abstain_reason
        self.selected_memories = selected_memories or []
        self.normalized_fields = normalized_fields or {}


class NoMemoryBaseline:
    name = "no_memory"

    def ingest(self, episode: Episode) -> None:
        return None

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        return BaselineResult("ABSTAIN", "no stored memory")


class FlatLexicalRagBaseline:
    name = "flat_lexical_rag"

    def __init__(self) -> None:
        self.episodes: List[Episode] = []

    def ingest(self, episode: Episode) -> None:
        self.episodes.append(episode)

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        scored = []
        for episode in self.episodes:
            score = lexical_score(request.query, episode.content)
            if score > 0:
                scored.append((score, episode))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return BaselineResult("ABSTAIN", "no lexical match")
        best_score, best_episode = scored[0]
        return BaselineResult(
            best_episode.content,
            "selected_episode=%s score=%.3f" % (best_episode.id, best_score),
            provenance=[best_episode.id],
        )


class LongContextLatestBaseline:
    name = "long_context_latest"

    def __init__(self) -> None:
        self.episodes: List[Episode] = []

    def ingest(self, episode: Episode) -> None:
        self.episodes.append(episode)

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        matches = []
        for episode in self.episodes:
            score = lexical_score(request.query, episode.content)
            if score > 0:
                matches.append((episode.timestamp, score, episode))
        matches.sort(key=lambda item: item[0], reverse=True)
        if not matches:
            return BaselineResult("ABSTAIN", "no current-context match")
        _, score, episode = matches[0]
        return BaselineResult(
            episode.content,
            "latest_episode=%s score=%.3f" % (episode.id, score),
            provenance=[episode.id],
        )


class HybridLexicalTemporalRagBaseline:
    """Flat retrieval with a recency boost, but no memory governance."""

    name = "hybrid_lexical_temporal_rag"

    def __init__(self) -> None:
        self.episodes: List[Episode] = []

    def ingest(self, episode: Episode) -> None:
        self.episodes.append(episode)

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        if not self.episodes:
            return BaselineResult("ABSTAIN", "no stored episodes")
        newest = max(episode.timestamp for episode in self.episodes)
        oldest = min(episode.timestamp for episode in self.episodes)
        span = max((newest - oldest).total_seconds(), 1.0)
        scored: List[Tuple[float, Episode]] = []
        for episode in self.episodes:
            query_score = lexical_score(request.query, episode.content)
            if query_score == 0:
                continue
            recency = (episode.timestamp - oldest).total_seconds() / span
            score = 0.75 * query_score + 0.25 * recency
            scored.append((score, episode))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return BaselineResult("ABSTAIN", "no hybrid match")
        score, episode = scored[0]
        return BaselineResult(
            episode.content,
            "selected_episode=%s score=%.3f" % (episode.id, score),
            provenance=[episode.id],
        )


class GraphLikeTemporalBaseline:
    """Temporal structured-memory baseline without governance.

    It uses the same deterministic extractor, stores structured temporal facts,
    invalidates older subject/relation facts in the same scope, and supports
    current plus as-of retrieval. It intentionally does not enforce deletion,
    do-not-use or consent policy.
    """

    name = "graph_like_temporal_baseline"

    def __init__(self, extractor: Optional[DeterministicExtractor] = None) -> None:
        self.store = InMemoryStore()
        self.extractor = extractor or DeterministicExtractor()

    def ingest(self, episode: Episode) -> None:
        self.store.add_episode(episode)
        for candidate in self.extractor.extract(episode):
            if candidate.type not in ("semantic_fact", "preference", "procedure"):
                continue
            metadata = candidate.metadata
            subject = str(metadata.get("subject") or candidate.user_id)
            relation = str(metadata.get("relation") or candidate.type)
            object_value = str(metadata.get("object") or candidate.claim)
            supersedes: List[str] = []
            for old_fact in self.store.matching_active_facts(subject, relation, candidate.user_id, candidate.project_id):
                if old_fact.object != object_value:
                    old_fact.invalid_at = episode.timestamp
                    self.store.update_fact(old_fact)
                    supersedes.append(old_fact.id)
            fact = TemporalFact(
                subject=subject,
                relation=relation,
                object=object_value,
                valid_at=episode.timestamp,
                confidence=candidate.confidence,
                evidence=list(candidate.evidence_episode_ids),
                supersedes=supersedes,
                scope={"user_id": candidate.user_id, "project_id": candidate.project_id},
                privacy_policy="normal",
            )
            self.store.add_fact(fact)
            for old_id in supersedes:
                old_fact = self.store.get_fact(old_id)
                if old_fact is not None:
                    old_fact.superseded_by = fact.id
                    self.store.update_fact(old_fact)

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        scored: List[Tuple[float, TemporalFact]] = []
        for fact in self.store.list_facts(user_id=request.user_id, project_id=request.project_id):
            if request.time_scope == "current" and fact.invalid_at is not None:
                continue
            if request.time_scope == "as_of_date":
                if request.as_of is None:
                    continue
                if fact.valid_at > request.as_of:
                    continue
                if fact.invalid_at is not None and fact.invalid_at <= request.as_of:
                    continue
            score = lexical_score(request.query, fact.claim_text)
            if score > 0:
                temporal_boost = 0.1 if fact.invalid_at is None else 0.0
                scored.append((clamp(score + temporal_boost), fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return BaselineResult("ABSTAIN", "no temporal fact match")
        selected = [fact for _, fact in scored[: request.top_k]]
        answer = " | ".join(fact.claim_text for fact in selected)
        provenance = sorted({eid for fact in selected for eid in fact.evidence})
        return BaselineResult(
            answer,
            "selected_facts=%s" % ",".join(fact.id for fact in selected),
            provenance=provenance,
        )


class Mem0ExternalMemoryBaseline:
    """Optional Mem0-backed baseline.

    The backend is injected so the default benchmark never imports or requires
    Mem0. Use `Mem0Backend` for a real optional run or a fake backend in tests.
    """

    name = "mem0_external"

    def __init__(self, backend: ExternalMemoryBackend, namespace: str = "") -> None:
        self.backend = backend
        self.namespace = namespace

    def ingest(self, episode: Episode) -> None:
        self.backend.ingest(self._scoped_episode(episode))

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        result = self.backend.search(self._scoped_request(request))
        return BaselineResult(
            result.answer_text(),
            result.retrieval_trace,
            provenance=result.provenance,
            abstain_reason=result.abstain_reason,
            selected_memories=[memory.to_dict() for memory in result.selected_memories],
            normalized_fields=dict(result.metadata),
        )

    def cleanup(self, request: RetrievalRequest) -> None:
        delete_all = getattr(self.backend, "delete_all", None)
        if delete_all is None:
            return
        delete_all(self._scoped_user_id(request.user_id) if self.namespace else request.user_id)

    def _scoped_episode(self, episode: Episode) -> Episode:
        if not self.namespace:
            return episode
        return replace(episode, user_id=self._scoped_user_id(episode.user_id))

    def _scoped_request(self, request: RetrievalRequest) -> RetrievalRequest:
        if not self.namespace:
            return request
        return replace(request, user_id=self._scoped_user_id(request.user_id))

    def _scoped_user_id(self, user_id: str) -> str:
        return "%s:%s" % (self.namespace, user_id)
