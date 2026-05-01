from __future__ import annotations

from typing import Iterable, List, Optional

from .adapters.base import TemporalGraphBackend
from .adapters.local import LocalTemporalGraphBackend
from .extractor import DeterministicExtractor
from .models import (
    Episode,
    EventContext,
    EventParticipant,
    EventRelation,
    MemoryCandidate,
    MemoryEvent,
    TemporalFact,
    ensure_datetime,
    tokenize,
)
from .policy import PolicyStore
from .reference import DeterministicReferenceResolver, ReferenceResolver
from .safety import unsafe_memory_reason
from .scope import infer_scope_from_fact_parts
from .source import source_conflict_decision, source_metadata_for, source_rank
from .store import InMemoryStore


class MemoryController:
    """The only durable write authority for the prototype."""

    def __init__(
        self,
        store: Optional[InMemoryStore] = None,
        policy: Optional[PolicyStore] = None,
        extractor: Optional[DeterministicExtractor] = None,
        temporal_backend: Optional[TemporalGraphBackend] = None,
        reference_resolver: Optional[ReferenceResolver] = None,
    ) -> None:
        if temporal_backend is None:
            self.policy = policy or PolicyStore()
            self.temporal_backend = LocalTemporalGraphBackend(store=store, policy=self.policy)
        else:
            self.temporal_backend = temporal_backend
            self.policy = policy or getattr(temporal_backend, "policy", None) or PolicyStore()

        self.store = getattr(self.temporal_backend, "store", store or InMemoryStore())
        self.extractor = extractor or DeterministicExtractor()
        self.reference_resolver = reference_resolver or DeterministicReferenceResolver()

    def ingest_episode(self, episode: Episode) -> List[MemoryCandidate]:
        self.temporal_backend.add_episode(episode)
        candidates = self.extractor.extract(episode)
        for candidate in candidates:
            self.apply_candidate(candidate, episode=episode)
        return candidates

    def propose_memory(self, candidate: MemoryCandidate, episode: Optional[Episode] = None) -> Optional[TemporalFact]:
        return self.apply_candidate(candidate, episode=episode)

    def apply_candidate(self, candidate: MemoryCandidate, episode: Optional[Episode] = None) -> Optional[TemporalFact]:
        self.temporal_backend.add_candidate(candidate)
        quarantine_reason = self._candidate_quarantine_reason(candidate)
        if quarantine_reason:
            candidate.metadata["quarantine_reason"] = quarantine_reason
            candidate.metadata["untrusted_instruction_content"] = quarantine_reason == "possible_prompt_injection"
            candidate.recommended_action = "ignore"
            candidate.risk_level = "high"
            self.temporal_backend.audit("candidate_quarantined", "%s:%s" % (candidate.id, quarantine_reason))
            return None

        action = self.policy.evaluate_candidate(candidate)

        if action == "ignore":
            self.temporal_backend.audit("candidate_ignored", candidate.id)
            return None
        if action == "ask_consent":
            self.temporal_backend.audit("candidate_requires_consent", candidate.id)
            return None
        if action == "delete":
            term = self._policy_term(candidate)
            if not term:
                return None
            self.request_forget(term, user_id=candidate.user_id, project_id=candidate.project_id)
            return None
        if action == "do_not_use":
            term = self._policy_term(candidate)
            if not term:
                return None
            self.request_do_not_use(term, user_id=candidate.user_id, project_id=candidate.project_id)
            return None
        if action not in ("store", "update_existing", "invalidate_existing"):
            self.temporal_backend.audit("candidate_unknown_action", candidate.id)
            return None

        return self._store_candidate_as_fact(candidate, episode=episode)

    def _policy_term(self, candidate: MemoryCandidate) -> str:
        term = str(candidate.metadata.get("deletion_term") or "").strip()
        if term:
            return term
        reference_type = str(candidate.metadata.get("reference_type") or "").strip()
        if not reference_type:
            return candidate.claim
        resolution = self.reference_resolver.resolve(
            reference_type=reference_type,
            facts=self.temporal_backend.list_facts(user_id=candidate.user_id, project_id=candidate.project_id),
            policy=self.policy,
            user_id=candidate.user_id,
            project_id=candidate.project_id,
        )
        if resolution.resolved:
            self.temporal_backend.audit("reference_resolved", "%s:%s" % (reference_type, resolution.term))
            return resolution.term
        candidate.metadata["unresolved_reference"] = True
        candidate.metadata["reference_resolution_reason"] = resolution.reason
        self.temporal_backend.audit("reference_unresolved", "%s:%s" % (reference_type, resolution.reason))
        return ""

    def request_forget(self, term: str, user_id: str, project_id: str) -> List[str]:
        self.policy.apply_deletion_term(term)
        affected: List[str] = []
        term_tokens = tokenize(term)
        for fact in self.temporal_backend.list_facts(user_id=user_id, project_id=project_id):
            claim_tokens = tokenize(fact.claim_text)
            if term.lower() in fact.claim_text.lower() or (term_tokens and term_tokens <= claim_tokens):
                fact.privacy_policy = "deleted"
                self.policy.mark_memory_do_not_use(fact.id)
                self.policy.mark_evidence_deleted(fact.evidence)
                self.temporal_backend.update_fact(fact)
                affected.append(fact.id)

        for reflection in self.temporal_backend.list_reflections(user_id=user_id, project_id=project_id):
            if term.lower() in reflection.claim.lower():
                reflection.status = "invalidated"
                self.policy.mark_memory_do_not_use(reflection.id)
                self.temporal_backend.update_reflection(reflection)
                affected.append(reflection.id)

        self.temporal_backend.audit("forget_requested", term)
        return affected

    def request_do_not_use(self, term: str, user_id: str, project_id: str) -> List[str]:
        self.policy.apply_deletion_term(term)
        affected: List[str] = []
        term_tokens = tokenize(term)
        for fact in self.temporal_backend.list_facts(user_id=user_id, project_id=project_id):
            claim_tokens = tokenize(fact.claim_text)
            if term.lower() in fact.claim_text.lower() or (term_tokens and term_tokens <= claim_tokens):
                fact.privacy_policy = "do_not_use"
                self.policy.mark_memory_do_not_use(fact.id)
                self.temporal_backend.update_fact(fact)
                affected.append(fact.id)

        for reflection in self.temporal_backend.list_reflections(user_id=user_id, project_id=project_id):
            if term.lower() in reflection.claim.lower():
                reflection.status = "invalidated"
                self.policy.mark_memory_do_not_use(reflection.id)
                self.temporal_backend.update_reflection(reflection)
                affected.append(reflection.id)

        self.temporal_backend.audit("do_not_use_requested", term)
        return affected

    def invalidate_facts(self, fact_ids: Iterable[str], invalid_at: Optional[object] = None) -> None:
        invalid_time = ensure_datetime(invalid_at)
        for fact_id in fact_ids:
            fact = self.temporal_backend.get_fact(fact_id)
            if fact is not None and fact.invalid_at is None:
                fact.invalid_at = invalid_time
                self.temporal_backend.update_fact(fact)

    def _store_candidate_as_fact(self, candidate: MemoryCandidate, episode: Optional[Episode] = None) -> TemporalFact:
        metadata = candidate.metadata
        timestamp = episode.timestamp if episode is not None else candidate.created_at
        subject = str(metadata.get("subject") or candidate.user_id)
        relation = str(metadata.get("relation") or candidate.type)
        object_value = str(metadata.get("object") or candidate.claim)
        privacy_policy = "sensitive" if metadata.get("sensitivity") in ("high", "restricted") else "normal"
        source_defaults = source_metadata_for(str(metadata.get("source") or ""), memory_type=candidate.type)
        source_type = str(metadata.get("source_type") or source_defaults["source_type"])
        source_trust = str(metadata.get("source_trust") or source_defaults["source_trust"])
        source_timestamp = metadata.get("source_timestamp") or timestamp
        source_conflict_policy = str(metadata.get("source_conflict_policy") or source_defaults["source_conflict_policy"])

        supersedes: List[str] = []
        conflict_with: List[str] = []
        new_invalid_at = None
        for old_fact in self.temporal_backend.matching_active_facts(
            subject=subject,
            relation=relation,
            user_id=candidate.user_id,
            project_id=candidate.project_id,
        ):
            if old_fact.object != object_value:
                if timestamp < old_fact.valid_at:
                    if new_invalid_at is None or old_fact.valid_at < new_invalid_at:
                        new_invalid_at = old_fact.valid_at
                    continue
                decision = source_conflict_decision(
                    old_subject=subject,
                    old_source_type=old_fact.source_type,
                    old_source_trust=old_fact.source_trust,
                    new_source_type=source_type,
                    new_source_trust=source_trust,
                )
                if relation == "availability" and source_type == "user_statement" and old_fact.source_type in ("tool_record", "crm_record"):
                    decision = "supersede"
                if decision == "keep_existing":
                    self.temporal_backend.audit("source_conflict_kept_existing", "%s:%s" % (old_fact.id, candidate.id))
                    return old_fact
                if decision == "store_conflict":
                    conflict_with.append(old_fact.id)
                    old_fact.conflict_with = sorted(set(old_fact.conflict_with + [candidate.id]))
                    self.temporal_backend.update_fact(old_fact)
                    continue
                old_fact.invalid_at = timestamp
                self.temporal_backend.update_fact(old_fact)
                supersedes.append(old_fact.id)
            else:
                old_fact.evidence = sorted(set(old_fact.evidence + list(candidate.evidence_episode_ids)))
                old_fact.confidence = max(old_fact.confidence, candidate.confidence)
                if source_rank(source_trust) > source_rank(old_fact.source_trust):
                    old_fact.source_type = source_type
                    old_fact.source_trust = source_trust
                    old_fact.source_timestamp = source_timestamp
                    old_fact.source_conflict_policy = source_conflict_policy
                self.temporal_backend.update_fact(old_fact)
                return old_fact

        inferred_scope = infer_scope_from_fact_parts(
            subject=subject,
            relation=relation,
            object_value=object_value,
            project_id=candidate.project_id,
        ).to_dict()
        scope = dict(inferred_scope)
        scope.update(
            {
                "user_id": candidate.user_id,
                "project_id": candidate.project_id,
            }
        )
        for field in ("actor_type", "subject_id", "candidate_id", "client_id", "role_id", "scope_confidence"):
            if field in metadata:
                scope[field] = metadata[field]

        fact = TemporalFact(
            subject=subject,
            relation=relation,
            object=object_value,
            valid_at=timestamp,
            confidence=candidate.confidence,
            evidence=list(candidate.evidence_episode_ids),
            invalid_at=new_invalid_at,
            supersedes=supersedes,
            scope=scope,
            privacy_policy=privacy_policy,
            source_type=source_type,
            source_trust=source_trust,
            source_timestamp=source_timestamp,
            source_conflict_policy=source_conflict_policy,
            conflict_with=conflict_with,
        )
        self.temporal_backend.add_fact(fact)

        for old_fact_id in supersedes:
            old_fact = self.temporal_backend.get_fact(old_fact_id)
            if old_fact is not None:
                old_fact.superseded_by = fact.id
                self.temporal_backend.update_fact(old_fact)

        self._store_event_for_fact(fact, candidate, episode=episode)
        return fact

    def _store_event_for_fact(
        self,
        fact: TemporalFact,
        candidate: MemoryCandidate,
        episode: Optional[Episode] = None,
    ) -> MemoryEvent:
        timestamp = episode.timestamp if episode is not None else fact.valid_at
        context = EventContext(
            user_id=fact.user_id,
            project_id=fact.project_id,
            candidate_id=fact.candidate_id,
            client_id=fact.client_id,
            role_id=fact.role_id,
            subject_id=fact.subject_id,
            conversation_id=episode.context_id if episode is not None else "default",
            source_type=fact.source_type,
            source_trust=fact.source_trust,
            timestamp=timestamp,
            confidence=fact.scope_confidence,
        )
        relation_type = self._event_relation_type(fact, candidate)
        participants = self._event_participants(fact)
        relations = [
            EventRelation(
                type=relation_type,
                subject_id=fact.subject_id,
                object_id=self._event_object_id(fact),
                relation=fact.relation,
                value=fact.object,
                valid_at=fact.valid_at,
                invalid_at=fact.invalid_at,
                evidence=list(fact.evidence),
                confidence=fact.confidence,
            )
        ]
        if fact.client_id and relation_type != "applies_to_client":
            relations.append(
                EventRelation(
                    type="applies_to_client",
                    subject_id=fact.subject_id,
                    object_id=fact.client_id,
                    relation="client_context",
                    value=fact.client_id,
                    valid_at=fact.valid_at,
                    evidence=list(fact.evidence),
                    confidence=fact.confidence,
                )
            )
        if fact.role_id and relation_type != "applies_to_role":
            relations.append(
                EventRelation(
                    type="applies_to_role",
                    subject_id=fact.subject_id,
                    object_id=fact.role_id,
                    relation="role_context",
                    value=fact.role_id,
                    valid_at=fact.valid_at,
                    evidence=list(fact.evidence),
                    confidence=fact.confidence,
                )
            )
        event = MemoryEvent(
            event_type=relation_type,
            content=self._event_content(fact),
            timestamp=timestamp,
            participants=participants,
            relations=relations,
            context=context,
            evidence_episode_ids=list(fact.evidence),
            confidence=fact.confidence,
            source_fact_id=fact.id,
        )
        self.temporal_backend.add_event(event)
        self._synchronize_candidate_client_context(event)
        return event

    def _event_relation_type(self, fact: TemporalFact, candidate: MemoryCandidate) -> str:
        explicit = str(candidate.metadata.get("event_relation_type") or "")
        if explicit:
            return explicit
        if fact.source_type == "recruiter_note":
            return "inferred_assumption"
        if fact.relation in ("target_client", "client"):
            return "applies_to_client"
        if fact.relation == "objection":
            return "objection_resolved" if fact.object in ("resolved", "cleared") else "objection_raised"
        if fact.relation == "status" and fact.subject.startswith("pitch_"):
            return "pitch_blocked" if "blocked" in fact.object else "pitch_allowed"
        if fact.actor_type in ("client", "role") and fact.relation in (
            "budget",
            "required_skill",
            "location_policy",
            "seniority",
        ):
            return "stated_requirement"
        return "expressed_preference"

    def _event_participants(self, fact: TemporalFact) -> List[EventParticipant]:
        participants = [
            EventParticipant(
                entity_id=fact.subject_id,
                entity_type=fact.actor_type or "unknown",
                role="subject",
                confidence=fact.scope_confidence,
            )
        ]
        if fact.candidate_id and fact.candidate_id != fact.subject_id:
            participants.append(EventParticipant(fact.candidate_id, "candidate", role="candidate", confidence=fact.scope_confidence))
        if fact.client_id:
            participants.append(EventParticipant(fact.client_id, "client", role="client_context", confidence=fact.scope_confidence))
        if fact.role_id:
            participants.append(EventParticipant(fact.role_id, "role", role="role_context", confidence=fact.scope_confidence))
        if fact.relation in ("former_company", "current_employer", "employer", "avoid_company"):
            participants.append(
                EventParticipant(
                    entity_id=self._normalize_company(fact.object),
                    entity_type="company",
                    role="employer" if fact.relation in ("former_company", "current_employer", "employer") else "blocked_company",
                    confidence=fact.confidence,
                )
            )
        return participants

    def _event_object_id(self, fact: TemporalFact) -> str:
        if fact.relation in ("target_client", "client"):
            return fact.client_id or self._normalize_client_id(fact.object)
        if fact.relation in ("former_company", "current_employer", "employer", "avoid_company"):
            return self._normalize_company(fact.object)
        if fact.role_id:
            return fact.role_id
        return ""

    def _event_content(self, fact: TemporalFact) -> str:
        pieces = [fact.claim_text]
        if fact.candidate_id and fact.candidate_id != fact.subject:
            pieces.append("candidate_context %s" % fact.candidate_id)
        if fact.client_id:
            pieces.append("applies_to_client %s" % fact.client_id)
        if fact.role_id:
            pieces.append("applies_to_role %s" % fact.role_id)
        if fact.source_type == "recruiter_note":
            pieces.append("source inferred_assumption")
        return " ".join(pieces)

    def _synchronize_candidate_client_context(self, event: MemoryEvent) -> None:
        if not event.context.candidate_id:
            return
        if self._has_relation_type(event, "applies_to_client"):
            self._apply_client_context_to_same_time_events(event)
            return
        if not self._is_candidate_client_contextual(event):
            return
        client_id = self._single_same_time_client_context(event)
        if client_id:
            self._attach_client_context(event, client_id)

    def _apply_client_context_to_same_time_events(self, client_event: MemoryEvent) -> None:
        client_id = client_event.context.client_id or self._relation_object(client_event, "applies_to_client")
        if not client_id:
            return
        for event in self.temporal_backend.list_events(
            user_id=client_event.context.user_id,
            project_id=client_event.context.project_id,
        ):
            if event.id == client_event.id:
                continue
            if event.context.candidate_id != client_event.context.candidate_id:
                continue
            if event.context.client_id or not self._same_event_day(event, client_event):
                continue
            if not self._is_candidate_client_contextual(event):
                continue
            self._attach_client_context(event, client_id)

    def _single_same_time_client_context(self, event: MemoryEvent) -> str:
        client_ids = {
            candidate.context.client_id or self._relation_object(candidate, "applies_to_client")
            for candidate in self.temporal_backend.list_events(
                user_id=event.context.user_id,
                project_id=event.context.project_id,
            )
            if candidate.context.candidate_id == event.context.candidate_id
            and self._same_event_day(candidate, event)
            and self._has_relation_type(candidate, "applies_to_client")
        }
        client_ids = {client_id for client_id in client_ids if client_id}
        if len(client_ids) == 1:
            return next(iter(client_ids))
        return ""

    def _attach_client_context(self, event: MemoryEvent, client_id: str) -> None:
        if event.context.client_id == client_id:
            return
        event.context.client_id = client_id
        event.relations.append(
            EventRelation(
                type="applies_to_client",
                subject_id=event.context.subject_id,
                object_id=client_id,
                relation="client_context",
                value=client_id,
                valid_at=event.timestamp,
                evidence=list(event.evidence_episode_ids),
                confidence=event.confidence,
            )
        )
        event.content = "%s applies_to_client %s" % (event.content, client_id)
        self.temporal_backend.update_event(event)

    def _is_candidate_client_contextual(self, event: MemoryEvent) -> bool:
        return any(
            relation.relation
            in (
                "salary_expectation",
                "salary_target",
                "work_mode",
                "notice_period",
                "relocation",
                "objection",
                "competing_offer",
                "status",
                "skill",
            )
            for relation in event.relations
        )

    def _has_relation_type(self, event: MemoryEvent, relation_type: str) -> bool:
        return any(relation.type == relation_type for relation in event.relations)

    def _relation_object(self, event: MemoryEvent, relation_type: str) -> str:
        for relation in event.relations:
            if relation.type == relation_type:
                return relation.object_id or relation.value
        return ""

    def _same_event_day(self, left: MemoryEvent, right: MemoryEvent) -> bool:
        return left.timestamp.date() == right.timestamp.date()

    def _normalize_client_id(self, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if not normalized.startswith("client_"):
            normalized = "client_%s" % normalized
        return normalized

    def _normalize_company(self, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if not normalized.startswith("company_"):
            normalized = "company_%s" % normalized
        return normalized

    def _candidate_quarantine_reason(self, candidate: MemoryCandidate) -> str:
        if candidate.type == "constraint" or candidate.recommended_action in ("delete", "do_not_use"):
            return ""
        metadata = candidate.metadata
        text = "%s %s" % (candidate.claim, metadata.get("object", ""))
        return unsafe_memory_reason(
            text,
            sensitivity=str(metadata.get("sensitivity") or "low"),
            consent_basis=str(metadata.get("consent_basis") or "implicit"),
        )
