from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import (
    ConsolidatedMemory,
    ConsolidationCandidate,
    ConsolidationDecision,
    ConsolidationRun,
    RetrievalRequest,
    TemporalFact,
    add_days,
    now_utc,
)
from .policy import PolicyStore
from .safety import instruction_risk_reason, sensitive_risk_reason
from .store import InMemoryStore


class SleepCycle:
    """Dry-run evidence consolidation.

    MVP 3 starts as a review queue, not autonomous truth creation. The engine
    proposes decisions with evidence and reasons. It does not mutate facts,
    events, reflections or retrieval ranking unless a caller explicitly records
    the returned run as audit data.
    """

    def __init__(
        self,
        store: InMemoryStore,
        policy: Optional[PolicyStore] = None,
        min_evidence: int = 2,
        allow_single_evidence: bool = False,
    ) -> None:
        self.store = store
        self.policy = policy or PolicyStore()
        self.min_evidence = 1 if allow_single_evidence else max(2, min_evidence)
        self.allow_single_evidence = allow_single_evidence

    def consolidate(
        self,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        *,
        apply: bool = False,
        record: bool = False,
    ) -> ConsolidationRun:
        """Return a dry-run consolidation plan.

        ``apply`` is deliberately reserved for a future implementation. MVP 3.0
        must not create durable truth automatically.
        """

        if apply:
            raise NotImplementedError("SleepCycle --apply is reserved; durable consolidation is not implemented.")
        return self.plan(user_id=user_id, project_id=project_id, record=record)

    def plan(
        self,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        *,
        record: bool = False,
    ) -> ConsolidationRun:
        user_scope = user_id or "user"
        project_scope = project_id or "default"
        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        audit: List[str] = []

        safe_facts, unsafe_decisions = self._safe_active_facts(user_id=user_id, project_id=project_id)
        decisions.extend(unsafe_decisions)
        candidates.extend(self._candidate_for_decision(decision) for decision in unsafe_decisions)

        contradiction_candidates, contradiction_decisions = self._contradiction_decisions(safe_facts)
        candidates.extend(contradiction_candidates)
        decisions.extend(contradiction_decisions)

        reflection_candidates, reflection_decisions = self._reflection_decisions(
            safe_facts=safe_facts,
            inactive_facts=self._inactive_facts(user_id=user_id, project_id=project_id),
        )
        candidates.extend(reflection_candidates)
        decisions.extend(reflection_decisions)

        decay_candidates, decay_decisions = self._decay_decisions(user_id=user_id, project_id=project_id)
        candidates.extend(decay_candidates)
        decisions.extend(decay_decisions)

        event_candidates, event_decisions = self._event_decisions(user_id=user_id, project_id=project_id)
        candidates.extend(event_candidates)
        decisions.extend(event_decisions)

        candidates = self._unique_candidates(candidates)
        decisions = self._unique_decisions(decisions)
        summary = self._summary(candidates, decisions)
        audit.append(
            "planned:%s candidates:%s decisions:%s review_required:%s"
            % (now_utc().isoformat(), len(candidates), len(decisions), summary["review_required"])
        )
        run_id = self._stable_id(
            "cr",
            [
                user_scope,
                project_scope,
                ",".join(candidate.id for candidate in candidates),
                ",".join(decision.id for decision in decisions),
            ],
        )
        run = ConsolidationRun(
            user_id=user_scope,
            project_id=project_scope,
            status="dry_run",
            candidates=candidates,
            decisions=decisions,
            summary=summary,
            audit_log=audit,
            id=run_id,
        )
        if record:
            self.store.add_consolidation_run(run)
        return run

    def decay_due_reflections(
        self,
        *,
        apply: bool = False,
        record: bool = False,
    ) -> ConsolidationRun:
        if apply:
            raise NotImplementedError("Reflection decay apply is reserved; decay is report-only in MVP 3.0.")
        candidates, decisions = self._reflection_decay_decisions()
        run = ConsolidationRun(
            status="dry_run",
            candidates=candidates,
            decisions=decisions,
            summary=self._summary(candidates, decisions),
            audit_log=["planned_reflection_decay:%s" % now_utc().isoformat()],
            id=self._stable_id("cr", ["reflection_decay", ",".join(decision.id for decision in decisions)]),
        )
        if record:
            self.store.add_consolidation_run(run)
        return run

    def _safe_active_facts(
        self,
        user_id: Optional[str],
        project_id: Optional[str],
    ) -> Tuple[List[TemporalFact], List[ConsolidationDecision]]:
        safe: List[TemporalFact] = []
        rejected: List[ConsolidationDecision] = []
        for fact in self.store.active_facts(user_id=user_id, project_id=project_id):
            reason = self._consolidation_exclusion_reason(fact)
            if reason:
                rejected.append(
                    self._decision(
                        action="no_op",
                        reason=reason,
                        evidence_ids=fact.evidence,
                        confidence=0.0,
                        scope=self._scope_for_fact(fact),
                        memory_type="semantic_fact",
                        review_required=reason in ("sensitive_requires_review", "possible_prompt_injection"),
                        target_id=fact.id,
                    )
                )
                continue
            safe.append(fact)
        return safe, rejected

    def _inactive_facts(self, user_id: Optional[str], project_id: Optional[str]) -> List[TemporalFact]:
        return [
            fact
            for fact in self.store.list_facts(user_id=user_id, project_id=project_id)
            if fact.invalid_at is not None and fact.evidence
        ]

    def _reflection_decisions(
        self,
        safe_facts: List[TemporalFact],
        inactive_facts: List[TemporalFact],
    ) -> Tuple[List[ConsolidationCandidate], List[ConsolidationDecision]]:
        by_scope_subject: Dict[str, List[TemporalFact]] = defaultdict(list)
        for fact in safe_facts:
            key = self._scope_key(fact, include_relation=False)
            by_scope_subject[key].append(fact)

        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        existing_claims = {reflection.claim for reflection in self.store.list_reflections()}
        for _, scoped_facts in by_scope_subject.items():
            evidence_ids = sorted({eid for fact in scoped_facts for eid in fact.evidence})
            subject = scoped_facts[0].subject
            relations = sorted({fact.relation for fact in scoped_facts})
            scope = self._scope_for_fact(scoped_facts[0])
            relation_type = self._consolidated_relation_type(relations)
            scope["relation_type"] = relation_type
            claim = "%s has recurring memory context around %s" % (subject, ", ".join(relations[:4]))
            if len(evidence_ids) < self.min_evidence:
                candidates.append(
                    self._candidate(
                        claim=claim,
                        scope=scope,
                        evidence_ids=evidence_ids,
                        confidence=0.0,
                        risk_flags=["insufficient_evidence"],
                    )
                )
                decisions.append(
                    self._decision(
                        action="no_op",
                        reason="insufficient_evidence",
                        evidence_ids=evidence_ids,
                        confidence=0.0,
                        scope=scope,
                    )
                )
                continue

            counter_evidence = sorted(
                {
                    evidence_id
                    for fact in inactive_facts
                    if self._same_consolidation_scope(fact, scoped_facts[0])
                    and fact.subject == subject
                    for evidence_id in fact.evidence
                }
            )
            confidence = min(0.85, 0.45 + 0.1 * min(len(evidence_ids), 4))
            if counter_evidence:
                confidence = max(0.2, confidence - 0.1)
            proposed = ConsolidatedMemory(
                claim=claim,
                memory_type="reflection",
                scope=scope,
                evidence_ids=evidence_ids,
                counter_evidence_ids=counter_evidence,
                confidence=confidence,
                reflection_type=self._reflection_type_for_scope(scope),
                status="proposed",
                last_reinforced_at=now_utc(),
                review_after=add_days(now_utc(), 30),
                id=self._stable_id("cm", [claim, ",".join(evidence_ids)]),
            )
            candidate = self._candidate(
                claim=claim,
                scope=scope,
                evidence_ids=evidence_ids,
                counter_evidence_ids=counter_evidence,
                confidence=confidence,
                proposed_memory=proposed,
            )
            candidates.append(candidate)
            decisions.append(
                self._decision(
                    action="update_reflection" if claim in existing_claims else "create_reflection",
                    reason="repeated_evidence",
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=counter_evidence,
                    confidence=confidence,
                    scope=scope,
                    review_required=bool(counter_evidence),
                    proposed_memory=proposed,
                    candidate_id=candidate.id,
                )
            )
        return candidates, decisions

    def _contradiction_decisions(
        self,
        safe_facts: List[TemporalFact],
    ) -> Tuple[List[ConsolidationCandidate], List[ConsolidationDecision]]:
        grouped: Dict[str, List[TemporalFact]] = defaultdict(list)
        for fact in safe_facts:
            grouped[self._scope_key(fact, include_relation=True)].append(fact)

        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        for facts in grouped.values():
            objects = {fact.object for fact in facts}
            conflicts = sorted({conflict for fact in facts for conflict in fact.conflict_with})
            if len(objects) <= 1 and not conflicts:
                continue
            evidence_ids = sorted({eid for fact in facts for eid in fact.evidence})
            scope = self._scope_for_fact(facts[0])
            claim = "%s has conflicting %s evidence" % (facts[0].subject, facts[0].relation)
            candidate = self._candidate(
                claim=claim,
                scope=scope,
                evidence_ids=evidence_ids,
                counter_evidence_ids=conflicts,
                confidence=0.0,
                risk_flags=["conflicting_evidence"],
            )
            candidates.append(candidate)
            decisions.append(
                self._decision(
                    action="flag_conflict",
                    reason="conflicting_evidence",
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=conflicts,
                    confidence=0.0,
                    scope=scope,
                    review_required=True,
                    candidate_id=candidate.id,
                )
            )
        return candidates, decisions

    def _decay_decisions(
        self,
        user_id: Optional[str],
        project_id: Optional[str],
    ) -> Tuple[List[ConsolidationCandidate], List[ConsolidationDecision]]:
        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        for fact in self.store.list_facts(user_id=user_id, project_id=project_id):
            if fact.invalid_at is None and not fact.superseded_by:
                continue
            if fact.privacy_policy in ("deleted", "do_not_use"):
                continue
            scope = self._scope_for_fact(fact)
            claim = "%s %s superseded memory should decay" % (fact.subject, fact.relation)
            metadata = {
                "decay_score": 1.0,
                "last_reinforced_at": fact.valid_at,
                "review_after": add_days(fact.invalid_at or now_utc(), 30),
                "archived": True,
            }
            candidate = self._candidate(
                claim=claim,
                memory_type="semantic_fact",
                scope=scope,
                evidence_ids=fact.evidence,
                confidence=fact.confidence,
                source_ids=[fact.id],
                risk_flags=["stale_or_superseded"],
            )
            candidates.append(candidate)
            decisions.append(
                self._decision(
                    action="decay",
                    reason="stale_or_superseded",
                    evidence_ids=fact.evidence,
                    confidence=fact.confidence,
                    scope=scope,
                    memory_type="semantic_fact",
                    target_id=fact.id,
                    decay_metadata=metadata,
                    candidate_id=candidate.id,
                )
            )
        return candidates, decisions

    def _event_decisions(
        self,
        user_id: Optional[str],
        project_id: Optional[str],
    ) -> Tuple[List[ConsolidationCandidate], List[ConsolidationDecision]]:
        grouped: Dict[str, List[Any]] = defaultdict(list)
        for event in self.store.list_events(user_id=user_id, project_id=project_id):
            if event.status in ("deleted", "do_not_use"):
                continue
            if instruction_risk_reason(event.claim_text):
                continue
            for relation in event.relations:
                if relation.relation == "client_context":
                    continue
                key = "|".join(
                    [
                        event.context.user_id,
                        event.context.project_id,
                        event.context.candidate_id,
                        event.context.client_id,
                        event.context.role_id,
                        relation.type,
                        relation.relation,
                    ]
                )
                grouped[key].append((event, relation))

        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        for items in grouped.values():
            evidence_ids = sorted({eid for event, _ in items for eid in event.evidence_episode_ids})
            if len(evidence_ids) < self.min_evidence:
                continue
            event, relation = items[0]
            scope = {
                "user_id": event.context.user_id,
                "project_id": event.context.project_id,
                "actor_type": self._actor_type_for_event_scope(event.context.candidate_id, event.context.client_id, event.context.role_id),
                "candidate_id": event.context.candidate_id,
                "client_id": event.context.client_id,
                "role_id": event.context.role_id,
                "subject_id": event.context.subject_id,
                "scope_confidence": event.context.confidence,
                "relation_type": relation.relation or relation.type,
            }
            claim = "%s has recurring event context around %s" % (
                event.context.subject_id or relation.subject_id,
                relation.relation or relation.type,
            )
            proposed = ConsolidatedMemory(
                claim=claim,
                memory_type="reflection",
                scope=scope,
                evidence_ids=evidence_ids,
                confidence=min(0.8, 0.45 + 0.1 * min(len(evidence_ids), 4)),
                reflection_type=self._reflection_type_for_scope(scope),
                status="proposed",
                id=self._stable_id("cm", [claim, ",".join(evidence_ids)]),
            )
            candidate = self._candidate(
                claim=claim,
                scope=scope,
                evidence_ids=evidence_ids,
                confidence=proposed.confidence,
                source_ids=[event.id for event, _ in items],
                proposed_memory=proposed,
            )
            candidates.append(candidate)
            decisions.append(
                self._decision(
                    action="create_reflection",
                    reason="repeated_event_evidence",
                    evidence_ids=evidence_ids,
                    confidence=proposed.confidence,
                    scope=scope,
                    proposed_memory=proposed,
                    candidate_id=candidate.id,
                )
            )
        return candidates, decisions

    def _reflection_decay_decisions(self) -> Tuple[List[ConsolidationCandidate], List[ConsolidationDecision]]:
        now = now_utc()
        candidates: List[ConsolidationCandidate] = []
        decisions: List[ConsolidationDecision] = []
        for reflection in self.store.list_reflections():
            if reflection.next_review_at > now or not reflection.is_active():
                continue
            scope = {
                "user_id": reflection.user_id,
                "project_id": reflection.project_id,
                "scope": reflection.scope,
                "actor_type": reflection.actor_type,
                "candidate_id": reflection.candidate_id,
                "client_id": reflection.client_id,
                "role_id": reflection.role_id,
                "subject_id": reflection.subject_id,
                "relation_type": reflection.relation_type,
                "scope_confidence": reflection.scope_confidence,
            }
            confidence = max(0.0, reflection.confidence - reflection.decay_rate)
            metadata = {
                "decay_score": min(1.0, reflection.decay_score + reflection.decay_rate),
                "last_reinforced_at": reflection.last_reinforced_at or reflection.last_reviewed,
                "review_after": now + timedelta(days=30),
                "archived": confidence < 0.2,
            }
            candidate = self._candidate(
                claim=reflection.claim,
                scope=scope,
                evidence_ids=reflection.supporting_evidence,
                counter_evidence_ids=reflection.counter_evidence,
                confidence=confidence,
                source_ids=[reflection.id],
                risk_flags=["decay_due"],
            )
            candidates.append(candidate)
            decisions.append(
                self._decision(
                    action="decay",
                    reason="review_due",
                    evidence_ids=reflection.supporting_evidence,
                    counter_evidence_ids=reflection.counter_evidence,
                    confidence=confidence,
                    scope=scope,
                    target_id=reflection.id,
                    decay_metadata=metadata,
                    candidate_id=candidate.id,
                )
            )
        return candidates, decisions

    def _consolidation_exclusion_reason(self, fact: TemporalFact) -> str:
        if fact.privacy_policy in ("deleted", "do_not_use"):
            return fact.privacy_policy
        if fact.id in self.policy.do_not_use_memory_ids:
            return "do_not_use"
        if any(evidence_id in self.policy.deleted_episode_ids for evidence_id in fact.evidence):
            return "deleted_evidence"
        if self.policy.matches_do_not_use_term(fact.claim_text):
            return "do_not_use_term"
        if instruction_risk_reason(fact.claim_text):
            return "possible_prompt_injection"
        if fact.privacy_policy == "sensitive" or sensitive_risk_reason(fact.claim_text):
            return "sensitive_requires_review"
        if not fact.evidence:
            return "missing_provenance"
        return ""

    def _candidate(
        self,
        *,
        claim: str,
        scope: Dict[str, Any],
        evidence_ids: List[str],
        confidence: float,
        memory_type: str = "reflection",
        counter_evidence_ids: Optional[List[str]] = None,
        source_ids: Optional[List[str]] = None,
        risk_flags: Optional[List[str]] = None,
        proposed_memory: Optional[ConsolidatedMemory] = None,
    ) -> ConsolidationCandidate:
        counter_evidence_ids = sorted(counter_evidence_ids or [])
        source_ids = sorted(source_ids or [])
        risk_flags = sorted(risk_flags or [])
        return ConsolidationCandidate(
            claim=claim,
            memory_type=memory_type,
            scope=dict(scope),
            evidence_ids=sorted(evidence_ids),
            counter_evidence_ids=counter_evidence_ids,
            confidence=confidence,
            source_ids=source_ids,
            risk_flags=risk_flags,
            proposed_memory=proposed_memory,
            id=self._stable_id("cc", [claim, memory_type, ",".join(sorted(evidence_ids)), ",".join(risk_flags)]),
        )

    def _candidate_for_decision(self, decision: ConsolidationDecision) -> ConsolidationCandidate:
        return self._candidate(
            claim="%s:%s" % (decision.reason, decision.target_id or decision.memory_type),
            memory_type=decision.memory_type,
            scope=decision.scope,
            evidence_ids=decision.evidence_ids,
            counter_evidence_ids=decision.counter_evidence_ids,
            confidence=decision.confidence,
            source_ids=[decision.target_id] if decision.target_id else [],
            risk_flags=[decision.reason],
        )

    def _decision(
        self,
        *,
        action: str,
        reason: str,
        evidence_ids: List[str],
        confidence: float,
        scope: Dict[str, Any],
        memory_type: str = "reflection",
        counter_evidence_ids: Optional[List[str]] = None,
        review_required: bool = False,
        target_id: str = "",
        proposed_memory: Optional[ConsolidatedMemory] = None,
        decay_metadata: Optional[Dict[str, Any]] = None,
        candidate_id: str = "",
    ) -> ConsolidationDecision:
        counter_evidence_ids = sorted(counter_evidence_ids or [])
        evidence_ids = sorted(evidence_ids)
        candidate_key = candidate_id or self._stable_id("cc", [reason, action, target_id, ",".join(evidence_ids)])
        return ConsolidationDecision(
            candidate_id=candidate_key,
            action=action,
            reason=reason,
            evidence_ids=evidence_ids,
            counter_evidence_ids=counter_evidence_ids,
            confidence=confidence,
            scope=dict(scope),
            memory_type=memory_type,
            review_required=review_required,
            target_id=target_id,
            proposed_memory=proposed_memory,
            decay_metadata=dict(decay_metadata or {}),
            id=self._stable_id(
                "cd",
                [
                    candidate_key,
                    action,
                    reason,
                    target_id,
                    ",".join(evidence_ids),
                    ",".join(counter_evidence_ids),
                ],
            ),
        )

    def _scope_for_fact(self, fact: TemporalFact) -> Dict[str, Any]:
        return {
            "user_id": fact.user_id,
            "project_id": fact.project_id,
            "actor_type": fact.actor_type,
            "candidate_id": fact.candidate_id,
            "client_id": fact.client_id,
            "role_id": fact.role_id,
            "subject_id": fact.subject_id,
            "scope_confidence": fact.scope_confidence,
            "subject": fact.subject,
            "relation": fact.relation,
            "relation_type": fact.relation,
        }

    def _consolidated_relation_type(self, relations: List[str]) -> str:
        if len(relations) == 1:
            return relations[0]
        return "profile"

    def _actor_type_for_event_scope(self, candidate_id: str, client_id: str, role_id: str) -> str:
        if candidate_id and client_id:
            return "mixed"
        if candidate_id:
            return "candidate"
        if client_id:
            return "client"
        if role_id:
            return "role"
        return "unknown"

    def _reflection_type_for_scope(self, scope: Dict[str, Any]) -> str:
        actor_type = str(scope.get("actor_type") or "unknown")
        relation_type = str(scope.get("relation_type") or scope.get("relation") or "")
        if actor_type == "candidate":
            return "candidate_preference"
        if actor_type == "client":
            return "client_requirement"
        if actor_type == "role":
            return "role_requirement"
        if actor_type == "project":
            return "project_pattern"
        if actor_type == "mixed":
            if relation_type in ("pitch_blocked", "do_not_contact", "do_not_mention", "objection"):
                return "risk_warning"
            return "candidate_preference"
        if actor_type in ("user", "unknown") and str(scope.get("subject") or scope.get("subject_id")) == "user":
            return "user_preference"
        return "unresolved_hypothesis"

    def _scope_key(self, fact: TemporalFact, *, include_relation: bool) -> str:
        parts = [
            fact.user_id,
            fact.project_id,
            fact.actor_type,
            fact.candidate_id,
            fact.client_id,
            fact.role_id,
            fact.subject_id,
            fact.subject,
        ]
        if include_relation:
            parts.append(fact.relation)
        return "|".join(str(part) for part in parts)

    def _same_consolidation_scope(self, left: TemporalFact, right: TemporalFact) -> bool:
        return self._scope_key(left, include_relation=False) == self._scope_key(right, include_relation=False)

    def _unique_candidates(self, candidates: Iterable[ConsolidationCandidate]) -> List[ConsolidationCandidate]:
        deduped: Dict[str, ConsolidationCandidate] = {}
        for candidate in candidates:
            deduped[candidate.id] = candidate
        return [deduped[key] for key in sorted(deduped)]

    def _unique_decisions(self, decisions: Iterable[ConsolidationDecision]) -> List[ConsolidationDecision]:
        deduped: Dict[str, ConsolidationDecision] = {}
        for decision in decisions:
            deduped[decision.id] = decision
        return [deduped[key] for key in sorted(deduped)]

    def _summary(
        self,
        candidates: List[ConsolidationCandidate],
        decisions: List[ConsolidationDecision],
    ) -> Dict[str, Any]:
        by_action: Dict[str, int] = defaultdict(int)
        for decision in decisions:
            by_action[decision.action] += 1
        return {
            "candidate_count": len(candidates),
            "decision_count": len(decisions),
            "review_required": sum(1 for decision in decisions if decision.review_required),
            "actions": dict(sorted(by_action.items())),
            "durable_writes": 0,
        }

    def _stable_id(self, prefix: str, parts: List[str]) -> str:
        payload = "||".join(str(part) for part in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return "%s_%s" % (prefix, digest)
