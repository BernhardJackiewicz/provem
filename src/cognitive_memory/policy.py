from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Set, Tuple

from .models import (
    Episode,
    MemoryCandidate,
    MemoryEvent,
    Reflection,
    RetrievalRequest,
    TemporalFact,
    ensure_datetime,
    tokenize,
)
from .safety import instruction_risk_reason, sensitive_risk_reason


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
        self.do_not_use_term_texts: List[str] = []
        self.legal_hold_memory_ids: Set[str] = set()
        # consent withdrawals: (normalized term, purpose); "" = all purposes
        self.revoked_consent_terms: Set[Tuple[str, str]] = set()
        # opt-in: quarantine candidates from sensitive channels (recruiter
        # notes etc.) unless consent is explicit
        self.enforce_channel_sensitivity: bool = False
        # opt-in semantic matcher for erased-term paraphrases; runtime
        # injected, deliberately not serialized (a function does not survive
        # a snapshot; re-inject after load)
        self.semantic_matcher = None
        self.semantic_threshold: float = 0.0
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
            # keep the raw surface form too: normalization destroys it, and a
            # semantic matcher has nothing to compare against sorted tokens
            if term not in self.do_not_use_term_texts:
                self.do_not_use_term_texts.append(term)
            self.audit_log.append("do_not_use_term:%s" % normalized)

    def mark_memory_do_not_use(self, memory_id: str) -> None:
        self.do_not_use_memory_ids.add(memory_id)
        self.audit_log.append("do_not_use_memory:%s" % memory_id)

    def revoke_consent(self, term: str, purpose: str = "") -> None:
        """Withdraw consent for a term, optionally scoped to one purpose.

        Not deletion: matching memories stay stored but are refused at
        retrieval with consent_revoked (for the revoked purpose, or entirely
        when purpose is empty).
        """
        normalized = " ".join(sorted(tokenize(term))) if tokenize(term) else term.lower().strip()
        if normalized:
            self.revoked_consent_terms.add((normalized, purpose))
            self.audit_log.append("consent_revoked:%s:%s" % (normalized, purpose or "*"))

    def _consent_revoked_reason(self, text: str, request: RetrievalRequest) -> Optional[str]:
        if not self.revoked_consent_terms:
            return None
        text_tokens = tokenize(text)
        declared = request.purpose or ""
        for normalized, purpose in self.revoked_consent_terms:
            term_tokens = tokenize(normalized)
            if term_tokens and term_tokens <= text_tokens and (not purpose or purpose == declared):
                return "consent_revoked"
        return None

    def mark_episode_deleted(self, episode_id: str) -> None:
        self.deleted_episode_ids.add(episode_id)
        self.audit_log.append("episode_deleted:%s" % episode_id)

    def mark_evidence_deleted(self, evidence_ids: Iterable[str]) -> None:
        for episode_id in evidence_ids:
            self.mark_episode_deleted(episode_id)

    def merge_from(self, other: "PolicyStore") -> int:
        """Union another policy's tombstone state into this one.

        Restore semantics: loading an older snapshot silently rolls back
        tombstones created after it was taken. The supported repair is to
        load the snapshot, then merge_from(current_policy) so no erasure or
        do-not-use state is lost. Idempotent. Returns how many entries were
        newly added.
        """
        added = 0
        for attr in ("deleted_episode_ids", "do_not_use_memory_ids",
                     "do_not_use_terms", "legal_hold_memory_ids"):
            mine: Set[str] = getattr(self, attr)
            theirs: Set[str] = getattr(other, attr)
            new = theirs - mine
            mine |= new
            added += len(new)
        if added:
            self.audit_log.append("merged_tombstones:%d" % added)
        return added

    def exclusion_reason(self, item: object, request: RetrievalRequest) -> Optional[str]:
        if isinstance(item, TemporalFact):
            return self._fact_exclusion_reason(item, request)
        if isinstance(item, MemoryEvent):
            return self._event_exclusion_reason(item, request)
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
        consent_reason = self._consent_revoked_reason(fact.claim_text, request)
        if consent_reason:
            return consent_reason
        # Purpose limitation: a declared purpose outside the fact's allowlist
        # is refused; no declared purpose means no purpose gating.
        if request.purpose and fact.allowed_purposes and request.purpose not in fact.allowed_purposes:
            return "purpose_mismatch"
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
        consent_reason = self._consent_revoked_reason(reflection.claim, request)
        if consent_reason:
            return consent_reason
        if request.memory_policy.require_provenance and len(reflection.supporting_evidence) < 2:
            return "weak_reflection_evidence"
        return None

    def _event_exclusion_reason(self, event: MemoryEvent, request: RetrievalRequest) -> Optional[str]:
        if event.id in self.do_not_use_memory_ids and request.memory_policy.exclude_do_not_use:
            return "do_not_use"
        if event.status in ("deleted", "do_not_use") and request.memory_policy.exclude_do_not_use:
            return event.status
        if event.context.user_id != request.user_id:
            return "wrong_user"
        if event.context.project_id != request.project_id:
            return "wrong_project"
        if request.memory_policy.require_provenance and not event.evidence_episode_ids:
            return "missing_provenance"
        if any(evidence_id in self.deleted_episode_ids for evidence_id in event.evidence_episode_ids):
            return "deleted_evidence"
        if self.matches_do_not_use_term(event.claim_text):
            return "do_not_use_term"
        consent_reason = self._consent_revoked_reason(event.claim_text, request)
        if consent_reason:
            return consent_reason
        # Purpose limitation: same gate as facts.
        if request.purpose and event.allowed_purposes and request.purpose not in event.allowed_purposes:
            return "purpose_mismatch"
        if instruction_risk_reason(event.claim_text) or sensitive_risk_reason(event.claim_text):
            return "possible_prompt_injection"
        if request.time_scope == "current" and not event.is_active():
            return event.status
        if request.time_scope == "as_of_date":
            as_of = request.as_of
            if as_of is None:
                return "missing_as_of_date"
            target = ensure_datetime(as_of)
            if event.timestamp > target:
                return "not_yet_valid"
        return None

    def set_semantic_matcher(self, matcher, threshold: float = 0.8) -> None:
        """Enable paraphrase matching for erased terms.

        ``matcher(text, term)`` returns a similarity in [0,1]. Consulted by
        matches_do_not_use_term after the token check, which covers facts,
        events and reflections (all exclusion paths funnel through it).
        """
        self.semantic_matcher = matcher
        self.semantic_threshold = float(threshold)

    def matches_do_not_use_term(self, text: str) -> bool:
        text_tokens = tokenize(text)
        for term in self.do_not_use_terms:
            term_tokens = tokenize(term)
            if term_tokens and term_tokens <= text_tokens:
                return True
        if self.semantic_matcher is not None and self.semantic_threshold > 0.0:
            for term_text in self.do_not_use_term_texts:
                try:
                    if float(self.semantic_matcher(text, term_text)) >= self.semantic_threshold:
                        return True
                except Exception:
                    # fail open: token matching stays the deterministic baseline
                    self.audit_log.append("semantic_matcher_error")
                    return False
        return False

    def _matches_do_not_use_term(self, text: str) -> bool:
        return self.matches_do_not_use_term(text)

    def to_dict(self) -> dict:
        return {
            "deleted_episode_ids": sorted(self.deleted_episode_ids),
            "do_not_use_memory_ids": sorted(self.do_not_use_memory_ids),
            "do_not_use_terms": sorted(self.do_not_use_terms),
            "do_not_use_term_texts": list(self.do_not_use_term_texts),
            "legal_hold_memory_ids": sorted(self.legal_hold_memory_ids),
            "revoked_consent_terms": sorted(list(pair) for pair in self.revoked_consent_terms),
            "enforce_channel_sensitivity": self.enforce_channel_sensitivity,
            "audit_log": list(self.audit_log),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PolicyStore":
        policy = cls()
        policy.deleted_episode_ids = set(data.get("deleted_episode_ids", []))
        policy.do_not_use_memory_ids = set(data.get("do_not_use_memory_ids", []))
        policy.do_not_use_terms = set(data.get("do_not_use_terms", []))
        policy.do_not_use_term_texts = list(data.get("do_not_use_term_texts", []))
        policy.legal_hold_memory_ids = set(data.get("legal_hold_memory_ids", []))
        policy.revoked_consent_terms = {
            (str(pair[0]), str(pair[1])) for pair in data.get("revoked_consent_terms", [])
        }
        policy.enforce_channel_sensitivity = bool(data.get("enforce_channel_sensitivity", False))
        policy.audit_log = list(data.get("audit_log", []))
        return policy


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
