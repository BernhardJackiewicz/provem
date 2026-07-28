from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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
    tokenize,
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


class OpenConversationRetrievalPlanner:
    """Retrieval strategy for open conversational QA.

    This is intentionally separate from the governed retrieval path. It ranks
    conversational memories with generic lexical, entity, relation and temporal
    cues, while still respecting deletion/do-not-use style policy exclusions.
    """

    STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "s",
        "she",
        "still",
        "that",
        "the",
        "their",
        "there",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "would",
    }
    RELATION_CUE_TOKENS = {
        "after",
        "before",
        "career",
        "date",
        "day",
        "dating",
        "dislike",
        "dislikes",
        "drink",
        "drinks",
        "eat",
        "eats",
        "field",
        "friend",
        "friends",
        "go",
        "going",
        "hate",
        "hates",
        "like",
        "likes",
        "live",
        "lives",
        "location",
        "love",
        "loves",
        "married",
        "move",
        "moved",
        "now",
        "plan",
        "planning",
        "pursue",
        "relationship",
        "status",
        "time",
        "year",
        "years",
    }
    TEMPORAL_WORDS = {
        "today",
        "tomorrow",
        "yesterday",
        "tonight",
        "morning",
        "afternoon",
        "evening",
        "week",
        "weeks",
        "weekend",
        "month",
        "months",
        "year",
        "years",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "ago",
        "next",
        "last",
        "current",
        "now",
        "later",
        "before",
        "after",
    }
    HARD_EXCLUSION_REASONS = {
        "deleted",
        "do_not_use",
        "deleted_evidence",
        "do_not_use_term",
        "sensitive",
        "wrong_project",
    }

    def __init__(
        self,
        store: InMemoryStore,
        policy: PolicyStore,
        min_top_score: float = 0.45,
        min_confidence: float = 0.32,
        min_score_margin: float = 0.04,
    ) -> None:
        self.store = store
        self.policy = policy
        self.min_top_score = min_top_score
        self.min_confidence = min_confidence
        self.min_score_margin = min_score_margin

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        query = _OpenConversationQuery.from_text(request.query, self.STOPWORDS, self.RELATION_CUE_TOKENS)
        excluded: List[ExcludedMemory] = []
        scored: List[Tuple[float, SelectedMemory, object, Dict[str, Any]]] = []
        latest_timestamp = self._latest_timestamp(request)

        for fact in self.store.list_facts(user_id=request.user_id, project_id=request.project_id):
            reason = self._hard_exclusion_reason(fact, request)
            if reason:
                excluded.append(ExcludedMemory(fact.id, reason, "temporal_fact", fact.claim_text))
                continue
            score, features = self._score_record(
                query=query,
                claim=fact.claim_text,
                subject=fact.subject,
                relation=fact.relation,
                object_value=fact.object,
                memory_type="temporal_fact",
                timestamp=fact.valid_at,
                latest_timestamp=latest_timestamp,
            )
            if score > 0:
                scored.append(
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
                        features,
                    )
                )

        for event in self.store.list_events(user_id=request.user_id, project_id=request.project_id):
            reason = self._hard_exclusion_reason(event, request)
            if reason:
                excluded.append(ExcludedMemory(event.id, reason, "memory_event", event.claim_text))
                continue
            relation = self._event_relation(event)
            object_value = self._event_value(event)
            score, features = self._score_record(
                query=query,
                claim=event.claim_text,
                subject=event.context.subject_id,
                relation=relation,
                object_value=object_value,
                memory_type="memory_event",
                timestamp=event.timestamp,
                latest_timestamp=latest_timestamp,
            )
            if score > 0:
                scored.append(
                    (
                        score,
                        SelectedMemory(
                            id=event.id,
                            memory_type="memory_event",
                            claim=event.claim_text,
                            score=score,
                            confidence=event.confidence,
                            evidence=list(event.evidence_episode_ids),
                        ),
                        event,
                        features,
                    )
                )

        for reflection in self.store.list_reflections(user_id=request.user_id, project_id=request.project_id):
            reason = self._hard_exclusion_reason(reflection, request)
            if reason:
                excluded.append(ExcludedMemory(reflection.id, reason, "reflection", reflection.claim))
                continue
            score, features = self._score_record(
                query=query,
                claim=reflection.claim,
                subject=reflection.subject_id,
                relation=reflection.relation_type,
                object_value="",
                memory_type="reflection",
                timestamp=reflection.created_at,
                latest_timestamp=latest_timestamp,
            )
            if score > 0:
                scored.append(
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
                        features,
                    )
                )

        scored.sort(key=lambda item: (item[0], item[1].confidence), reverse=True)
        selected_items = self._diverse_top_k(scored, request.top_k)
        selected_memories = [memory for _, memory, _, _ in selected_items]
        top_score = selected_items[0][0] if selected_items else 0.0
        second_score = selected_items[1][0] if len(selected_items) > 1 else 0.0
        score_margin = top_score - second_score
        top_features = selected_items[0][3] if selected_items else {}
        confidence = self._aggregate_confidence(selected_memories)
        low_confidence = bool(selected_items) and (top_score < self.min_top_score or confidence < self.min_confidence)
        low_margin = bool(selected_items) and len(selected_items) > 1 and score_margin < self.min_score_margin and top_score < 0.62
        relation_mismatch = bool(
            selected_items
            and query.strong_relation
            and float(top_features.get("relation", 0.0)) == 0.0
            and float(top_features.get("temporal", 0.0)) == 0.0
            and float(top_features.get("content", 0.0)) < 0.5
        )

        if not selected_items or low_confidence or low_margin or relation_mismatch:
            if low_confidence:
                reason = "low_confidence_open_conversation"
            elif low_margin:
                reason = "low_margin_open_conversation"
            elif relation_mismatch:
                reason = "relation_mismatch_open_conversation"
            else:
                reason = "insufficient_evidence"
            for _, memory, _, _ in selected_items:
                excluded.append(ExcludedMemory(memory.id, reason, memory.memory_type, memory.claim))
            trace = self._trace(request, selected_memories, excluded, confidence, True, reason, selected_items, query)
            return RetrievalResult(
                selected_memories=selected_memories,
                excluded_memories=excluded,
                provenance=[],
                confidence=confidence,
                abstain_recommended=True,
                abstain_reason=reason,
                retrieval_trace=trace,
                metadata=self._metadata(top_score, second_score, score_margin, top_features),
            )

        provenance = sorted({evidence_id for memory in selected_memories for evidence_id in memory.evidence})
        trace = self._trace(request, selected_memories, excluded, confidence, False, "", selected_items, query)
        return RetrievalResult(
            selected_memories=selected_memories,
            excluded_memories=excluded,
            provenance=provenance,
            confidence=confidence,
            abstain_recommended=False,
            abstain_reason="",
            retrieval_trace=trace,
            metadata=self._metadata(top_score, second_score, score_margin, top_features),
        )

    def _score_record(
        self,
        query: "_OpenConversationQuery",
        claim: str,
        subject: str,
        relation: str,
        object_value: str,
        memory_type: str,
        timestamp: object,
        latest_timestamp: object,
    ) -> Tuple[float, Dict[str, Any]]:
        subject_tokens = self._open_tokens(subject)
        relation_tokens = self._open_tokens(relation)
        object_tokens = self._open_tokens(object_value)
        claim_tokens = self._open_tokens("%s %s %s" % (claim, relation, object_value))
        content_overlap = self._overlap_ratio(query.content_tokens, claim_tokens)
        entity_overlap = 1.0 if subject_tokens and query.all_tokens & subject_tokens else 0.0
        relation_match = self._relation_match(query.all_tokens, relation, relation_tokens, object_tokens, claim_tokens)
        temporal_match = self._temporal_match(query.all_tokens, relation, object_tokens, claim_tokens)
        recency = self._recency_score(query.all_tokens, timestamp, latest_timestamp)
        memory_weight = {"temporal_fact": 0.06, "memory_event": 0.04, "reflection": 0.0}.get(memory_type, 0.0)
        score = (
            0.45 * content_overlap
            + 0.18 * entity_overlap
            + 0.24 * relation_match
            + 0.10 * temporal_match
            + recency
            + memory_weight
        )
        if query.strong_relation and relation_match == 0.0:
            score -= 0.12
        return clamp(score), {
            "content": round(content_overlap, 3),
            "entity": round(entity_overlap, 3),
            "relation": round(relation_match, 3),
            "temporal": round(temporal_match, 3),
            "recency": round(recency, 3),
            "session": self._session_bucket(timestamp),
        }

    def _hard_exclusion_reason(self, memory: object, request: RetrievalRequest) -> str:
        reason = self.policy.exclusion_reason(memory, request)
        return reason if reason in self.HARD_EXCLUSION_REASONS else ""

    def _relation_match(
        self,
        query_tokens: set,
        relation: str,
        relation_tokens: set,
        object_tokens: set,
        claim_tokens: set,
    ) -> float:
        if relation_tokens & query_tokens:
            return 0.8
        if query_tokens & {"where", "live", "lives", "stay", "stays", "move", "moved", "from", "location"}:
            if relation in ("location", "home", "address") or relation_tokens & {"location", "home", "address"}:
                return 1.0
        if query_tokens & {"when", "time", "date", "day", "month", "year", "years", "before", "after"}:
            if relation_tokens & {"time", "date", "day", "plan", "event", "schedule"} or object_tokens & self.TEMPORAL_WORDS:
                return 0.8
        if query_tokens & {"relationship", "status", "friend", "friends", "dating", "married", "single", "partner"}:
            if relation.startswith("relationship") or claim_tokens & {"friend", "friends", "dating", "married", "single", "partner"}:
                return 1.0
        if query_tokens & {"like", "likes", "love", "loves", "enjoy", "enjoys", "hate", "hates"}:
            if relation in ("likes", "dislikes"):
                return 1.0
        if query_tokens & {"drink", "drinks", "eat", "eats", "read", "reads", "play", "plays", "practice", "practices"}:
            if relation in ("habit", "routine", "likes"):
                return 0.9
        if query_tokens & {"career", "job", "field", "education", "pursue", "path", "plan", "goal"}:
            if relation_tokens & {"career", "job", "field", "education", "plan", "goal", "attribute"}:
                return 0.8
        return 0.0

    def _temporal_match(self, query_tokens: set, relation: str, object_tokens: set, claim_tokens: set) -> float:
        if not query_tokens & {"when", "time", "date", "day", "month", "year", "years", "before", "after", "now", "current", "still"}:
            return 0.0
        if object_tokens & self.TEMPORAL_WORDS or claim_tokens & self.TEMPORAL_WORDS:
            return 1.0
        if relation in ("plan", "event"):
            return 0.5
        return 0.0

    def _recency_score(self, query_tokens: set, timestamp: object, latest_timestamp: object) -> float:
        if not query_tokens & {"now", "current", "still", "latest"}:
            return 0.0
        timestamp_value = self._timestamp_value(timestamp)
        latest_value = self._timestamp_value(latest_timestamp)
        if timestamp_value is None or latest_value is None:
            return 0.0
        age_days = max(0.0, (latest_value - timestamp_value).total_seconds() / 86400.0)
        if age_days <= 0.01:
            return 0.06
        if age_days <= 1.0:
            return 0.05
        if age_days <= 7.0:
            return 0.03
        if age_days <= 30.0:
            return 0.015
        return 0.0

    def _diverse_top_k(
        self,
        scored: List[Tuple[float, SelectedMemory, object, Dict[str, Any]]],
        top_k: int,
    ) -> List[Tuple[float, SelectedMemory, object, Dict[str, Any]]]:
        selected: List[Tuple[float, SelectedMemory, object, Dict[str, Any]]] = []
        seen_sessions = set()
        seen_claims = set()
        for item in scored:
            claim_key = item[1].claim.lower()
            session = item[3].get("session", 0)
            if claim_key in seen_claims:
                continue
            if session and session in seen_sessions and len(scored) > top_k:
                continue
            selected.append(item)
            seen_claims.add(claim_key)
            if session:
                seen_sessions.add(session)
            if len(selected) >= top_k:
                return selected
        for item in scored:
            if item in selected:
                continue
            claim_key = item[1].claim.lower()
            if claim_key in seen_claims:
                continue
            selected.append(item)
            seen_claims.add(claim_key)
            if len(selected) >= top_k:
                break
        return selected

    def _aggregate_confidence(self, selected: List[SelectedMemory]) -> float:
        if not selected:
            return 0.0
        top_score = max(memory.score for memory in selected)
        weighted_confidence = sum(memory.confidence * memory.score for memory in selected)
        total_score = sum(memory.score for memory in selected)
        memory_confidence = weighted_confidence / total_score if total_score else 0.0
        return clamp(0.7 * top_score + 0.3 * memory_confidence)

    def _metadata(self, top_score: float, second_score: float, score_margin: float, top_features: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "retrieval_mode": "hybrid",
            "top_score": round(top_score, 4),
            "second_score": round(second_score, 4),
            "score_margin": round(score_margin, 4),
            "top_relation_match": float(top_features.get("relation", 0.0)) if top_features else 0.0,
            "top_temporal_match": float(top_features.get("temporal", 0.0)) if top_features else 0.0,
            "top_content_overlap": float(top_features.get("content", 0.0)) if top_features else 0.0,
        }

    def _trace(
        self,
        request: RetrievalRequest,
        selected: List[SelectedMemory],
        excluded: List[ExcludedMemory],
        confidence: float,
        abstain: bool,
        abstain_reason: str,
        selected_items: List[Tuple[float, SelectedMemory, object, Dict[str, Any]]],
        query: "_OpenConversationQuery",
    ) -> str:
        selected_ids = ",".join(memory.id for memory in selected) or "none"
        candidate_ids = ",".join("%s:%.3f" % (item[1].id, item[0]) for item in selected_items[:5]) or "none"
        excluded_summary = ",".join("%s:%s" % (item.id, item.reason) for item in excluded[:8]) or "none"
        top_features = selected_items[0][3] if selected_items else {}
        top_score = selected_items[0][0] if selected_items else 0.0
        second_score = selected_items[1][0] if len(selected_items) > 1 else 0.0
        return (
            "query=%r mode=hybrid task=%s selected=%s candidates=%s excluded=%s content_tokens=%s top_score=%.3f score_margin=%.3f relation=%.3f temporal=%.3f confidence=%.3f abstain=%s abstain_reason=%s"
            % (
                request.query,
                request.task_type,
                selected_ids,
                candidate_ids,
                excluded_summary,
                ",".join(sorted(query.content_tokens)) or "none",
                top_score,
                top_score - second_score,
                float(top_features.get("relation", 0.0)) if top_features else 0.0,
                float(top_features.get("temporal", 0.0)) if top_features else 0.0,
                confidence,
                abstain,
                abstain_reason or "none",
            )
        )

    def _event_relation(self, event: MemoryEvent) -> str:
        for relation in event.relations:
            if relation.relation:
                return relation.relation
        return event.event_type

    def _event_value(self, event: MemoryEvent) -> str:
        values = [relation.value or relation.object_id for relation in event.relations if relation.value or relation.object_id]
        return " ".join(values)

    def _latest_timestamp(self, request: RetrievalRequest) -> object:
        timestamps = []
        for fact in self.store.list_facts(user_id=request.user_id, project_id=request.project_id):
            value = self._timestamp_value(fact.valid_at)
            if value is not None:
                timestamps.append(value)
        for event in self.store.list_events(user_id=request.user_id, project_id=request.project_id):
            value = self._timestamp_value(event.timestamp)
            if value is not None:
                timestamps.append(value)
        for reflection in self.store.list_reflections(user_id=request.user_id, project_id=request.project_id):
            value = self._timestamp_value(reflection.created_at)
            if value is not None:
                timestamps.append(value)
        return max(timestamps) if timestamps else None

    def _session_bucket(self, timestamp: object) -> int:
        value = self._timestamp_value(timestamp)
        if value is None:
            return 0
        return value.date().toordinal()

    def _timestamp_value(self, timestamp: object) -> object:
        if timestamp is None:
            return None
        if hasattr(timestamp, "date") and hasattr(timestamp, "__sub__"):
            return timestamp
        return None

    def _open_tokens(self, text: str) -> set:
        return {token for token in tokenize(text) if token not in self.STOPWORDS}

    def _overlap_ratio(self, query_tokens: set, memory_tokens: set) -> float:
        if not query_tokens or not memory_tokens:
            return 0.0
        return len(query_tokens & memory_tokens) / float(len(query_tokens))


class _OpenConversationQuery:
    def __init__(self, all_tokens: set, content_tokens: set, strong_relation: bool) -> None:
        self.all_tokens = all_tokens
        self.content_tokens = content_tokens
        self.strong_relation = strong_relation

    @classmethod
    def from_text(cls, text: str, stopwords: set, relation_cues: set) -> "_OpenConversationQuery":
        all_tokens = {token for token in tokenize(text) if token not in stopwords}
        content_tokens = {token for token in all_tokens if token not in relation_cues}
        strong_relation = bool(all_tokens & relation_cues)
        return cls(all_tokens=all_tokens, content_tokens=content_tokens or all_tokens, strong_relation=strong_relation)
