from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Set

from .models import (
    Episode,
    MemoryCandidate,
    Reflection,
    RetrievalRequest,
    TemporalFact,
    ensure_datetime,
    tokenize,
)


class PolicyStore:
    """Consent, retention, deletion and use restrictions.

    The store is deliberately separate from the fact graph. A memory can remain
    historically present while becoming deleted, invalidated or forbidden for
    retrieval.
    """

    def __init__(self) -> None:
        self.deleted_episode_ids: Set[str] = set()
        self.do_not_use_memory_ids: Set[str] = set()
        self.do_not_use_terms: Set[str] = set()
        self.legal_hold_memory_ids: Set[str] = set()
        self.audit_log: List[str] = []

    def evaluate_candidate(self, candidate: MemoryCandidate) -> str:
        if candidate.recommended_action in ("ignore", "ask_consent"):
            return candidate.recommended_action
        if candidate.confidence < 0.3:
            return "ignore"
        if not candidate.evidence_episode_ids and candidate.created_by != "human":
            return "ignore"
        if candidate.risk_level == "high" and candidate.metadata.get("consent_basis") != "explicit":
            return "ask_consent"
        if candidate.metadata.get("sensitivity") in ("high", "restricted") and candidate.metadata.get("consent_basis") != "explicit":
            return "ask_consent"
        if candidate.metadata.get("consent_basis") == "none":
            return "ask_consent"
        return candidate.recommended_action

    def apply_deletion_term(self, term: str) -> None:
        normalized = " ".join(sorted(tokenize(term))) if tokenize(term) else term.lower().strip()
        if normalized:
            self.do_not_use_terms.add(normalized)
            self.audit_log.append("do_not_use_term:%s" % normalized)

    def mark_memory_do_not_use(self, memory_id: str) -> None:
        self.do_not_use_memory_ids.add(memory_id)
        self.audit_log.append("do_not_use_memory:%s" % memory_id)

    def mark_episode_deleted(self, episode_id: str) -> None:
        self.deleted_episode_ids.add(episode_id)
        self.audit_log.append("episode_deleted:%s" % episode_id)

    def mark_evidence_deleted(self, evidence_ids: Iterable[str]) -> None:
        for episode_id in evidence_ids:
            self.mark_episode_deleted(episode_id)

    def exclusion_reason(self, item: object, request: RetrievalRequest) -> Optional[str]:
        if isinstance(item, TemporalFact):
            return self._fact_exclusion_reason(item, request)
        if isinstance(item, Reflection):
            return self._reflection_exclusion_reason(item, request)
        return "unsupported_memory_type"

    def _fact_exclusion_reason(self, fact: TemporalFact, request: RetrievalRequest) -> Optional[str]:
        if fact.id in self.do_not_use_memory_ids and request.memory_policy.exclude_do_not_use:
            return "do_not_use"
        if fact.privacy_policy in ("deleted", "do_not_use") and request.memory_policy.exclude_do_not_use:
            return fact.privacy_policy
        if fact.project_id != request.project_id:
            return "wrong_project"
        if fact.user_id != request.user_id:
            return "wrong_user"
        if not request.memory_policy.allow_sensitive and fact.privacy_policy == "sensitive":
            return "sensitive"
        if any(evidence_id in self.deleted_episode_ids for evidence_id in fact.evidence):
            return "deleted_evidence"
        if self._matches_do_not_use_term(fact.claim_text):
            return "do_not_use_term"
        if request.time_scope == "current" and request.memory_policy.exclude_invalidated and fact.invalid_at is not None:
            return "invalidated"
        if request.time_scope == "as_of_date":
            as_of = request.as_of
            if as_of is None:
                return "missing_as_of_date"
            target = ensure_datetime(as_of)
            if fact.valid_at > target:
                return "not_yet_valid"
            if fact.invalid_at is not None and fact.invalid_at <= target:
                return "not_valid_as_of_date"
        if request.memory_policy.require_provenance and not fact.evidence:
            return "missing_provenance"
        return None

    def _reflection_exclusion_reason(self, reflection: Reflection, request: RetrievalRequest) -> Optional[str]:
        if not request.memory_policy.allow_reflections:
            return "reflections_disabled"
        if reflection.id in self.do_not_use_memory_ids and request.memory_policy.exclude_do_not_use:
            return "do_not_use"
        if reflection.user_id != request.user_id:
            return "wrong_user"
        if reflection.project_id != request.project_id:
            return "wrong_project"
        if not reflection.is_active():
            return reflection.status
        if any(evidence_id in self.deleted_episode_ids for evidence_id in reflection.supporting_evidence):
            return "deleted_evidence"
        if self._matches_do_not_use_term(reflection.claim):
            return "do_not_use_term"
        if request.memory_policy.require_provenance and len(reflection.supporting_evidence) < 2:
            return "weak_reflection_evidence"
        return None

    def _matches_do_not_use_term(self, text: str) -> bool:
        text_tokens = tokenize(text)
        for term in self.do_not_use_terms:
            term_tokens = tokenize(term)
            if term_tokens and term_tokens <= text_tokens:
                return True
        return False


def retention_expired(episode: Episode, at: Optional[datetime] = None) -> bool:
    target = ensure_datetime(at) if at is not None else ensure_datetime(None)
    if episode.retention_policy == "session":
        return episode.timestamp < target
    if episode.retention_policy.endswith("d"):
        try:
            days = int(episode.retention_policy[:-1])
        except ValueError:
            return False
        return (target - episode.timestamp).days >= days
    return False
