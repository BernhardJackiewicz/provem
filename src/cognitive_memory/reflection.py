from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional

from .models import Reflection, TemporalFact, add_days, now_utc
from .store import InMemoryStore


class SleepCycle:
    """Evidence-checked consolidation.

    Reflections are treated as hypotheses. They need multiple evidence points,
    carry confidence, and can be reviewed or invalidated later.
    """

    def __init__(self, store: InMemoryStore, min_evidence: int = 2, allow_single_evidence: bool = False) -> None:
        self.store = store
        self.min_evidence = 1 if allow_single_evidence else max(2, min_evidence)
        self.allow_single_evidence = allow_single_evidence

    def consolidate(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Reflection]:
        facts = [
            fact
            for fact in self.store.active_facts(user_id=user_id, project_id=project_id)
            if fact.privacy_policy == "normal" and fact.evidence
        ]
        inactive_facts = [
            fact
            for fact in self.store.list_facts(user_id=user_id, project_id=project_id)
            if fact.invalid_at is not None and fact.evidence
        ]
        by_scope_subject: Dict[str, List[TemporalFact]] = defaultdict(list)
        for fact in facts:
            key = "%s|%s|%s" % (fact.user_id, fact.project_id, fact.subject)
            by_scope_subject[key].append(fact)

        created: List[Reflection] = []
        existing_claims = {reflection.claim for reflection in self.store.list_reflections()}
        for _, scoped_facts in by_scope_subject.items():
            evidence_ids = sorted({eid for fact in scoped_facts for eid in fact.evidence})
            if len(evidence_ids) < self.min_evidence:
                continue

            relations = sorted({fact.relation for fact in scoped_facts})
            subject = scoped_facts[0].subject
            claim = "%s has recurring memory context around %s" % (subject, ", ".join(relations[:4]))
            if claim in existing_claims:
                continue
            counter_evidence = sorted(
                {
                    evidence_id
                    for fact in inactive_facts
                    if fact.user_id == scoped_facts[0].user_id
                    and fact.project_id == scoped_facts[0].project_id
                    and fact.subject == subject
                    for evidence_id in fact.evidence
                }
            )

            confidence = min(0.85, 0.45 + 0.1 * min(len(evidence_ids), 4))
            if counter_evidence:
                confidence = max(0.2, confidence - 0.1)
            reflection = Reflection(
                claim=claim,
                confidence=confidence,
                supporting_evidence=evidence_ids,
                counter_evidence=counter_evidence,
                scope="user" if subject == scoped_facts[0].user_id else "project",
                user_id=scoped_facts[0].user_id,
                project_id=scoped_facts[0].project_id,
                decay_rate=0.05,
                last_reviewed=now_utc(),
                next_review_at=add_days(now_utc(), 30),
                status="hypothesis",
            )
            self.store.add_reflection(reflection)
            created.append(reflection)
            existing_claims.add(claim)
        return created

    def decay_due_reflections(self) -> List[Reflection]:
        now = now_utc()
        updated: List[Reflection] = []
        for reflection in self.store.list_reflections():
            if reflection.next_review_at > now or not reflection.is_active():
                continue
            reflection.confidence = max(0.0, reflection.confidence - reflection.decay_rate)
            reflection.last_reviewed = now
            reflection.next_review_at = now + timedelta(days=30)
            if reflection.confidence < 0.2:
                reflection.status = "archived"
            self.store.update_reflection(reflection)
            updated.append(reflection)
        return updated
