from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .models import (
    ExcludedMemory,
    MemoryEvent,
    Reflection,
    RetrievalRequest,
    RetrievalResult,
    SelectedMemory,
    TemporalFact,
    clamp,
    lexical_score,
)
from .policy import PolicyStore
from .safety import instruction_risk_reason, sensitive_risk_reason
from .scope import (
    has_ambiguous_reference,
    infer_scope_from_query,
    reflection_scope_exclusion_reason,
    relation_matches,
    scope_exclusion_reason,
)
from .store import InMemoryStore


class RetrievalPlanner:
    """Policy-aware memory retrieval with traceable exclusions."""

    def __init__(self, store: InMemoryStore, policy: PolicyStore) -> None:
        self.store = store
        self.policy = policy

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        selected: List[Tuple[float, SelectedMemory, object]] = []
        eligible_facts: List[TemporalFact] = []
        excluded: List[ExcludedMemory] = []
        query_scope = infer_scope_from_query(request.query, project_id=request.project_id)

        for fact in self.store.list_facts():
            score = self._score_fact(request, fact)
            if request.task_type == "reflection":
                if score > 0:
                    excluded.append(
                        ExcludedMemory(
                            id=fact.id,
                            reason="fact_not_reflection",
                            memory_type="temporal_fact",
                            claim=fact.claim_text,
                        )
                    )
                continue
            scope_reason = scope_exclusion_reason(query_scope, fact)
            if scope_reason is not None:
                if score > 0:
                    excluded.append(
                        ExcludedMemory(
                            id=fact.id,
                            reason=scope_reason,
                            memory_type="temporal_fact",
                            claim=fact.claim_text,
                        )
                    )
                continue
            reason = self.policy.exclusion_reason(fact, request)
            if reason is not None:
                if score > 0 or reason in ("invalidated", "wrong_project", "deleted", "do_not_use", "deleted_evidence"):
                    excluded.append(
                        ExcludedMemory(
                            id=fact.id,
                            reason=reason,
                            memory_type="temporal_fact",
                            claim=fact.claim_text,
                        )
                    )
                continue
            if score >= request.min_score:
                eligible_facts.append(fact)
                selected.append(
                    (
                        score,
                        SelectedMemory(
                            id=fact.id,
                            memory_type="temporal_fact",
                            claim=fact.claim_text,
                            score=score,
                            confidence=fact.confidence,
                            evidence=list(fact.evidence),
                        ),
                        fact,
                    )
                )

        for reflection in self.store.list_reflections():
            score = self._score_reflection(request, reflection)
            scope_reason = reflection_scope_exclusion_reason(query_scope, reflection)
            if scope_reason is not None:
                if score > 0:
                    excluded.append(
                        ExcludedMemory(
                            id=reflection.id,
                            reason=scope_reason,
                            memory_type="reflection",
                            claim=reflection.claim,
                        )
                    )
                continue
            reason = self.policy.exclusion_reason(reflection, request)
            if reason is not None:
                if score > 0 or reason in ("wrong_project", "deleted_evidence", "weak_reflection_evidence"):
                    excluded.append(
                        ExcludedMemory(
                            id=reflection.id,
                            reason=reason,
                            memory_type="reflection",
                            claim=reflection.claim,
                        )
                    )
                continue
            if score >= request.min_score:
                selected.append(
                    (
                        score,
                        SelectedMemory(
                            id=reflection.id,
                            memory_type="reflection",
                            claim=reflection.claim,
                            score=score,
                            confidence=reflection.confidence,
                            evidence=list(reflection.supporting_evidence),
                        ),
                        reflection,
                    )
                )

        selected.sort(key=lambda item: item[0], reverse=True)
        event_memories, event_exclusions = self._event_memories_for_request(request, query_scope)
        excluded.extend(event_exclusions)
        if event_memories is not None and not has_ambiguous_reference(request.query):
            selected_memories = event_memories[: request.top_k]
            selected_facts = []
        else:
            selected_items = [] if has_ambiguous_reference(request.query) else selected[: request.top_k]
            selected_memories = [item for _, item, _ in selected_items]
            selected_facts = [item for _, _, item in selected_items if isinstance(item, TemporalFact)]
        safety_reason = self._safety_abstain_reason(request, query_scope, selected_facts, eligible_facts, excluded)
        if safety_reason:
            for fact in selected_facts:
                excluded.append(
                    ExcludedMemory(
                        id=fact.id,
                        reason=safety_reason,
                        memory_type="temporal_fact",
                        claim=fact.claim_text,
                    )
                )
            selected_memories = []
        provenance = sorted({evidence_id for memory in selected_memories for evidence_id in memory.evidence})
        confidence = self._aggregate_confidence(selected_memories)
        abstain = bool(safety_reason) or not selected_memories or confidence < 0.2
        abstain_reason = safety_reason or self._abstain_reason(request, selected_memories, excluded, confidence, abstain)
        trace = self._trace(request, selected_memories, excluded, confidence, abstain, abstain_reason)
        return RetrievalResult(
            selected_memories=selected_memories,
            excluded_memories=excluded,
            provenance=provenance,
            confidence=confidence,
            abstain_recommended=abstain,
            abstain_reason=abstain_reason,
            retrieval_trace=trace,
        )

    def _event_memories_for_request(
        self,
        request: RetrievalRequest,
        query_scope: object,
    ) -> Tuple[Optional[List[SelectedMemory]], List[ExcludedMemory]]:
        if getattr(query_scope, "actor_type", "") != "mixed":
            return None, []
        candidate_id = getattr(query_scope, "candidate_id", "")
        client_id = getattr(query_scope, "client_id", "")
        query_relation = getattr(query_scope, "relation", "")
        if not candidate_id or not client_id or not query_relation:
            return None, []

        excluded: List[ExcludedMemory] = []
        matching: List[Tuple[float, MemoryEvent]] = []
        for event in self.store.list_events(user_id=request.user_id, project_id=request.project_id):
            event_score = lexical_score(request.query, event.claim_text)
            if event.context.candidate_id and event.context.candidate_id != candidate_id:
                if event_score > 0:
                    excluded.append(ExcludedMemory(event.id, "wrong_scope", "memory_event", event.claim_text))
                continue
            if not event.context.candidate_id:
                continue
            event_client_id = event.context.client_id or self._event_relation_object(event, "applies_to_client")
            if event_client_id and event_client_id != client_id:
                if event_score > 0:
                    excluded.append(ExcludedMemory(event.id, "wrong_scope", "memory_event", event.claim_text))
                continue
            if not event_client_id:
                continue
            if not self._event_relation_matches(event, query_relation):
                if event_score > 0:
                    excluded.append(ExcludedMemory(event.id, "insufficient_evidence", "memory_event", event.claim_text))
                continue
            reason = self.policy.exclusion_reason(event, request)
            if reason:
                if event_score > 0 or reason in ("deleted_evidence", "do_not_use_term", "wrong_project"):
                    excluded.append(ExcludedMemory(event.id, reason, "memory_event", event.claim_text))
                continue
            if event_score > 0:
                matching.append((clamp(event_score + 0.2 * event.confidence), event))

        if not matching:
            return None, excluded

        current_events = self._latest_events_by_relation(matching, request)
        if self._event_values_conflict(current_events):
            for _, event in current_events:
                excluded.append(ExcludedMemory(event.id, "source_conflict", "memory_event", event.claim_text))
            return [], excluded
        current_events.sort(key=lambda item: item[0], reverse=True)
        selected = [
            SelectedMemory(
                id=event.id,
                memory_type="memory_event",
                claim=event.claim_text,
                score=score,
                confidence=event.confidence,
                evidence=list(event.evidence_episode_ids),
            )
            for score, event in current_events
        ]
        return selected, excluded

    def _latest_events_by_relation(
        self,
        matching: List[Tuple[float, MemoryEvent]],
        request: RetrievalRequest,
    ) -> List[Tuple[float, MemoryEvent]]:
        grouped: Dict[Tuple[str, str, str, str], Tuple[float, MemoryEvent]] = {}
        for score, event in matching:
            for relation in event.relations:
                if relation.relation == "client_context":
                    continue
                client_id = event.context.client_id or self._event_relation_object(event, "applies_to_client")
                key = (event.context.candidate_id, client_id, relation.relation, event.context.role_id)
                current = grouped.get(key)
                if current is None or event.timestamp > current[1].timestamp:
                    grouped[key] = (score, event)
        return list(grouped.values())

    def _event_values_conflict(self, events: List[Tuple[float, MemoryEvent]]) -> bool:
        grouped: Dict[Tuple[str, str, str], set] = {}
        for _, event in events:
            for relation in event.relations:
                if relation.relation == "client_context":
                    continue
                key = (event.context.candidate_id, event.context.client_id, relation.relation)
                grouped.setdefault(key, set()).add(relation.value)
        return any(len(values) > 1 for values in grouped.values())

    def _event_relation_matches(self, event: MemoryEvent, query_relation: str) -> bool:
        for relation in event.relations:
            if relation.relation == "client_context":
                continue
            if relation_matches(query_relation, relation.relation, "candidate"):
                return True
        return False

    def _event_relation_object(self, event: MemoryEvent, relation_type: str) -> str:
        for relation in event.relations:
            if relation.type == relation_type:
                return relation.object_id or relation.value
        return ""

    def _score_fact(self, request: RetrievalRequest, fact: TemporalFact) -> float:
        query_score = lexical_score(request.query, fact.claim_text)
        if query_score == 0.0:
            return 0.0
        confidence_component = 0.2 * fact.confidence
        temporal_component = 0.05 if fact.invalid_at is None else 0.0
        return clamp(0.75 * query_score + confidence_component + temporal_component)

    def _score_reflection(self, request: RetrievalRequest, reflection: Reflection) -> float:
        query_score = lexical_score(request.query, reflection.claim)
        if query_score == 0.0:
            return 0.0
        confidence_component = 0.2 * reflection.confidence
        return clamp(0.7 * query_score + confidence_component)

    def _safety_abstain_reason(
        self,
        request: RetrievalRequest,
        query_scope: object,
        selected_facts: List[TemporalFact],
        eligible_facts: List[TemporalFact],
        excluded: List[ExcludedMemory],
    ) -> str:
        if not selected_facts:
            if request.task_type == "compliance" and self._ambiguous_update_present(request):
                return "ambiguous_reference"
            return ""
        if request.task_type == "compliance" and self._ambiguous_update_present(request):
            return "ambiguous_reference"
        if self._selected_contains_unsafe_content(selected_facts):
            return "possible_prompt_injection"
        if self._has_source_conflict(eligible_facts):
            return "source_conflict"
        if self._has_ambiguous_identity(query_scope, selected_facts):
            return "ambiguous_identity"
        if any(item.reason == "wrong_scope" for item in excluded) and self._has_mixed_scope_query(query_scope):
            return "wrong_scope"
        return ""

    def _selected_contains_unsafe_content(self, selected_facts: List[TemporalFact]) -> bool:
        for fact in selected_facts:
            text = fact.claim_text
            if instruction_risk_reason(text) or sensitive_risk_reason(text):
                return True
        return False

    def _has_source_conflict(self, facts: List[TemporalFact]) -> bool:
        grouped: Dict[Tuple[str, str], List[TemporalFact]] = {}
        for fact in facts:
            grouped.setdefault((fact.subject, fact.relation), []).append(fact)
        for group in grouped.values():
            objects = {fact.object for fact in group}
            if len(objects) <= 1:
                continue
            source_types = {fact.source_type for fact in group}
            if len(source_types) > 1 or any(fact.conflict_with for fact in group):
                return True
        return False

    def _has_ambiguous_identity(self, query_scope: object, selected_facts: List[TemporalFact]) -> bool:
        if not self._has_mixed_scope_query(query_scope):
            return False
        candidate_id = getattr(query_scope, "candidate_id", "")
        client_id = getattr(query_scope, "client_id", "")
        if not candidate_id or not client_id:
            return False
        for fact in selected_facts:
            if fact.candidate_id != candidate_id:
                continue
            if fact.relation in ("target_client", "client", "stage", "status"):
                continue
            target_clients = {
                self._normalize_client_id(item.object)
                for item in self.store.list_facts(user_id=fact.user_id, project_id=fact.project_id)
                if item.subject == candidate_id and item.relation in ("target_client", "client")
            }
            if len(target_clients) > 1 and client_id in target_clients:
                return True
        return False

    def _has_mixed_scope_query(self, query_scope: object) -> bool:
        return getattr(query_scope, "actor_type", "") == "mixed"

    def _normalize_client_id(self, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if not normalized.startswith("client_"):
            normalized = "client_%s" % normalized
        return normalized

    def _ambiguous_update_present(self, request: RetrievalRequest) -> bool:
        ambiguous_candidate = any(
            candidate.user_id == request.user_id
            and candidate.project_id == request.project_id
            and bool(candidate.metadata.get("unresolved_reference"))
            for candidate in self.store.candidates.values()
        )
        if ambiguous_candidate:
            return True
        patterns = (
            r"\b(he|she|they|it)\s+changed\b",
            r"\bchanged\s+(it|that\s+one)\b",
            r"\bthat\s+one\s+changed\b",
            r"\bremove\s+that\s+one\b",
            r"\bforget\s+it\b",
        )
        for episode in self.store.list_episodes(user_id=request.user_id, project_id=request.project_id):
            lowered = episode.content.lower()
            if any(re.search(pattern, lowered) for pattern in patterns):
                return True
        return False

    def _aggregate_confidence(self, selected: List[SelectedMemory]) -> float:
        if not selected:
            return 0.0
        weighted = sum(memory.confidence * memory.score for memory in selected)
        total = sum(memory.score for memory in selected)
        if total == 0:
            return 0.0
        return clamp(weighted / total)

    def _abstain_reason(
        self,
        request: RetrievalRequest,
        selected: List[SelectedMemory],
        excluded: List[ExcludedMemory],
        confidence: float,
        abstain: bool,
    ) -> str:
        if not abstain:
            return ""
        if has_ambiguous_reference(request.query):
            return "ambiguous_reference"
        reasons = {item.reason for item in excluded}
        for reason in (
            "source_conflict",
            "possible_prompt_injection",
            "ambiguous_identity",
            "ambiguous_reference",
            "stale_or_superseded",
        ):
            if reason in reasons:
                return reason
        if reasons & {"deleted", "do_not_use", "deleted_evidence", "do_not_use_term", "sensitive"}:
            return "forbidden_memory"
        if "wrong_scope" in reasons:
            return "wrong_scope"
        if "insufficient_evidence" in reasons:
            return "insufficient_evidence"
        if selected and confidence < 0.2:
            return "low_confidence"
        return "insufficient_evidence"

    def _trace(
        self,
        request: RetrievalRequest,
        selected: List[SelectedMemory],
        excluded: List[ExcludedMemory],
        confidence: float,
        abstain: bool,
        abstain_reason: str,
    ) -> str:
        selected_ids = ",".join(memory.id for memory in selected) or "none"
        excluded_summary = ",".join("%s:%s" % (item.id, item.reason) for item in excluded) or "none"
        return (
            "query=%r task=%s time_scope=%s selected=%s excluded=%s confidence=%.3f abstain=%s abstain_reason=%s"
            % (request.query, request.task_type, request.time_scope, selected_ids, excluded_summary, confidence, abstain, abstain_reason or "none")
        )
