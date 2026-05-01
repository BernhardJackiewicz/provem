from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    ConsolidationDecision,
    ConsolidationRun,
    Reflection,
    ReviewDecision,
    ReviewItem,
    ReviewQueue,
    ReviewStatus,
    now_utc,
)
from .safety import instruction_risk_reason, sensitive_risk_reason


REVIEW_POLICIES = {"none", "approve_safe", "reject_all", "approve_low_risk_only"}
REVIEW_MODES = {"review_required", "all"}

_HIGH_RISK_REASONS = {
    "sensitive_requires_review",
    "possible_prompt_injection",
    "conflicting_evidence",
    "source_conflict",
    "deleted",
    "deleted_evidence",
    "do_not_use",
    "do_not_use_term",
    "forbidden_memory",
    "cross_scope",
    "scope_leakage",
    "policy_violation",
}
_MEDIUM_RISK_ACTIONS = {"decay", "archive", "flag_conflict", "no_op"}
_MEDIUM_REFLECTION_TYPES = {"client_requirement", "role_requirement", "procedural_rule", "risk_warning"}


def build_review_queue(run: ConsolidationRun, mode: str = "review_required") -> ReviewQueue:
    if mode not in REVIEW_MODES:
        raise ValueError("Unsupported review queue mode: %s" % mode)

    items: List[ReviewItem] = []
    for decision in run.decisions:
        if mode == "review_required" and not decision.review_required:
            continue
        items.append(_review_item_from_decision(run, decision))

    queue = ReviewQueue(
        consolidation_run_id=run.id,
        user_id=run.user_id,
        project_id=run.project_id,
        mode=mode,
        items=items,
        decisions=[],
        summary={},
        audit_log=["created:%s items:%s mode:%s" % (now_utc().isoformat(), len(items), mode)],
        id=_stable_id("rq", [run.id, mode, ",".join(item.id for item in items)]),
    )
    queue.summary = summarize_review_queue(queue)
    return queue


def simulate_review(
    queue: ReviewQueue,
    policy: str = "approve_low_risk_only",
    *,
    controller: Optional[Any] = None,
    reviewer_id: str = "simulated_reviewer",
) -> ReviewQueue:
    if policy not in REVIEW_POLICIES:
        raise ValueError("Unsupported simulated review policy: %s" % policy)
    if policy == "none":
        queue.summary = summarize_review_queue(queue)
        return queue

    queue.decisions = []
    conflict_items = [item for item in queue.items if item.risk_level == "high" or item.reason in _HIGH_RISK_REASONS]
    for item in queue.items:
        status, reason, confidence = _simulate_item_decision(item, policy, controller, conflict_items)
        item.status = status
        item.updated_at = now_utc()
        queue.decisions.append(
            ReviewDecision(
                review_item_id=item.id,
                status=status,
                reviewer_id=reviewer_id,
                reason=reason,
                decision_confidence=confidence,
                evidence_ids=list(item.evidence_ids),
                counter_evidence_ids=list(item.counter_evidence_ids),
                scope=dict(item.scope),
                risk_level=item.risk_level,
                proposed_action=item.proposed_action,
                id=_stable_id("rd", [queue.id, item.id, policy, status, reason]),
            )
        )
    queue.audit_log.append("simulated:%s policy:%s decisions:%s" % (now_utc().isoformat(), policy, len(queue.decisions)))
    queue.summary = summarize_review_queue(queue)
    return queue


def apply_approved_review_decisions_to_evaluation_copy(
    controller: Any,
    run: ConsolidationRun,
    queue: ReviewQueue,
) -> List[ConsolidationDecision]:
    """Apply approved review decisions to an isolated evaluation controller.

    This helper is intentionally named for evaluation. It should not be used as
    a production apply path.
    """

    decisions_by_id = {decision.id: decision for decision in run.decisions}
    approved: List[ConsolidationDecision] = []
    approved_item_ids = {
        decision.review_item_id
        for decision in queue.decisions
        if decision.status == ReviewStatus.APPROVED
    }
    for item in queue.items:
        if item.id not in approved_item_ids:
            continue
        decision = decisions_by_id.get(item.consolidation_decision_id)
        if decision is None or decision.proposed_memory is None:
            continue
        proposed = decision.proposed_memory
        reflection = Reflection(
            claim=proposed.claim,
            confidence=proposed.confidence,
            supporting_evidence=list(proposed.evidence_ids),
            counter_evidence=list(proposed.counter_evidence_ids),
            scope="project" if proposed.scope.get("project_id") not in ("", "default") else "user",
            user_id=str(proposed.scope.get("user_id") or "user"),
            project_id=str(proposed.scope.get("project_id") or "default"),
            actor_type=str(proposed.scope.get("actor_type") or "unknown"),
            candidate_id=str(proposed.scope.get("candidate_id") or ""),
            client_id=str(proposed.scope.get("client_id") or ""),
            role_id=str(proposed.scope.get("role_id") or ""),
            subject_id=str(proposed.scope.get("subject_id") or proposed.scope.get("subject") or ""),
            relation_type=str(proposed.scope.get("relation_type") or proposed.scope.get("relation") or ""),
            scope_confidence=float(proposed.scope.get("scope_confidence") or 0.0),
            reflection_type=proposed.reflection_type,
            decay_score=proposed.decay_score,
            last_reinforced_at=proposed.last_reinforced_at,
            review_after=proposed.review_after,
            archived=proposed.archived,
            status="hypothesis",
        )
        controller.store.add_reflection(reflection)
        approved.append(decision)
    return approved


def classify_review_risk(decision: ConsolidationDecision) -> str:
    text = decision.proposed_memory.claim if decision.proposed_memory is not None else ""
    reason = decision.reason.lower()
    actor_type = str(decision.scope.get("actor_type", ""))
    reflection_type = decision.proposed_memory.reflection_type if decision.proposed_memory is not None else ""
    if (
        decision.review_required
        or decision.counter_evidence_ids
        or decision.action == "flag_conflict"
        or reason in _HIGH_RISK_REASONS
        or "conflict" in reason
        or "sensitive" in reason
        or "deleted" in reason
        or "do_not_use" in reason
        or "prompt_injection" in reason
        or "cross_scope" in reason
        or instruction_risk_reason(text)
        or sensitive_risk_reason(text)
    ):
        return "high"
    if decision.action in _MEDIUM_RISK_ACTIONS:
        return "medium"
    if reflection_type in _MEDIUM_REFLECTION_TYPES or actor_type == "mixed":
        return "medium"
    if decision.action in ("create_reflection", "update_reflection") and decision.proposed_memory is not None:
        return "low"
    return "medium"


def summarize_review_queue(queue: ReviewQueue) -> Dict[str, Any]:
    status_counts: Dict[str, int] = defaultdict(int)
    risk_counts: Dict[str, int] = defaultdict(int)
    for item in queue.items:
        status_counts[item.status] += 1
        risk_counts[item.risk_level] += 1
    decision_status_counts: Dict[str, int] = defaultdict(int)
    high_risk_approved = 0
    for decision in queue.decisions:
        decision_status_counts[decision.status] += 1
        if decision.status == ReviewStatus.APPROVED and decision.risk_level == "high":
            high_risk_approved += 1
    return {
        "item_count": len(queue.items),
        "decision_count": len(queue.decisions),
        "status_counts": dict(sorted(status_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "decision_status_counts": dict(sorted(decision_status_counts.items())),
        "pending": status_counts.get(ReviewStatus.PENDING, 0),
        "approved": decision_status_counts.get(ReviewStatus.APPROVED, 0),
        "rejected": decision_status_counts.get(ReviewStatus.REJECTED, 0),
        "needs_more_evidence": decision_status_counts.get(ReviewStatus.NEEDS_MORE_EVIDENCE, 0),
        "deferred": decision_status_counts.get(ReviewStatus.DEFERRED, 0),
        "high_risk_autoapproved": high_risk_approved,
    }


def approved_review_item_ids(queue: ReviewQueue) -> List[str]:
    return sorted(
        decision.review_item_id
        for decision in queue.decisions
        if decision.status == ReviewStatus.APPROVED
    )


def rejected_review_items(queue: ReviewQueue) -> List[Dict[str, str]]:
    by_item = {item.id: item for item in queue.items}
    rejected = []
    for decision in queue.decisions:
        if decision.status == ReviewStatus.APPROVED:
            continue
        item = by_item.get(decision.review_item_id)
        rejected.append(
            {
                "id": item.consolidation_decision_id if item else decision.review_item_id,
                "review_item_id": decision.review_item_id,
                "status": decision.status,
                "reason": decision.reason,
                "risk_level": decision.risk_level,
            }
        )
    return rejected


def _review_item_from_decision(run: ConsolidationRun, decision: ConsolidationDecision) -> ReviewItem:
    risk_level = classify_review_risk(decision)
    proposed = decision.proposed_memory
    summary = proposed.claim if proposed is not None else ""
    if risk_level == "high" and (sensitive_risk_reason(summary) or instruction_risk_reason(summary)):
        summary = "<redacted_high_risk_review_claim>"
    return ReviewItem(
        consolidation_run_id=run.id,
        consolidation_decision_id=decision.id,
        candidate_id=decision.candidate_id,
        proposed_action=decision.action,
        reason=decision.reason,
        risk_level=risk_level,
        evidence_ids=list(decision.evidence_ids),
        counter_evidence_ids=list(decision.counter_evidence_ids),
        scope=dict(decision.scope),
        memory_type=decision.memory_type,
        proposed_memory_id=proposed.id if proposed is not None else "",
        proposed_memory_summary=summary,
        review_required=decision.review_required,
        id=_stable_id("ri", [run.id, decision.id]),
    )


def _simulate_item_decision(
    item: ReviewItem,
    policy: str,
    controller: Optional[Any],
    conflict_items: Sequence[ReviewItem],
) -> Tuple[str, str, float]:
    if policy == "reject_all":
        return ReviewStatus.REJECTED, "simulated_reject_all", 1.0
    if item.risk_level == "high":
        return ReviewStatus.REJECTED, "high_risk_requires_human_review", 0.95
    blocker = _approval_blocker(item, controller, conflict_items)
    if blocker:
        if blocker in ("insufficient_evidence", "counter_evidence_present", "stale_or_superseded_evidence", "missing_proposed_memory"):
            return ReviewStatus.NEEDS_MORE_EVIDENCE, blocker, 0.75
        if blocker == "unsupported_action":
            return ReviewStatus.DEFERRED, blocker, 0.75
        return ReviewStatus.REJECTED, blocker, 0.9
    if policy == "approve_low_risk_only" and item.risk_level != "low":
        return ReviewStatus.DEFERRED, "medium_risk_requires_human_review", 0.75
    return ReviewStatus.APPROVED, "simulated_%s" % policy, 0.8 if item.risk_level == "low" else 0.65


def _approval_blocker(
    item: ReviewItem,
    controller: Optional[Any],
    conflict_items: Sequence[ReviewItem],
) -> str:
    if item.proposed_action not in ("create_reflection", "update_reflection"):
        return "unsupported_action"
    if _scope_overlaps_any_conflict(item, conflict_items):
        return "scope_has_review_required_conflict"
    if not item.proposed_memory_id or not item.proposed_memory_summary:
        return "missing_proposed_memory"
    if len(item.evidence_ids) < 2:
        return "insufficient_evidence"
    if item.counter_evidence_ids:
        return "counter_evidence_present"
    if controller is not None:
        if _uses_stale_or_superseded_evidence(controller, item.evidence_ids):
            return "stale_or_superseded_evidence"
        if any(evidence_id in controller.policy.deleted_episode_ids for evidence_id in item.evidence_ids):
            return "deleted_evidence"
        if controller.policy.matches_do_not_use_term(item.proposed_memory_summary):
            return "do_not_use_term"
    if instruction_risk_reason(item.proposed_memory_summary):
        return "possible_prompt_injection"
    if sensitive_risk_reason(item.proposed_memory_summary):
        return "sensitive_content"
    return ""


def _uses_stale_or_superseded_evidence(controller: Any, evidence_ids: Sequence[str]) -> bool:
    evidence = set(evidence_ids)
    for fact in controller.store.list_facts():
        if not evidence.intersection(fact.evidence):
            continue
        if fact.invalid_at is not None or fact.superseded_by:
            return True
    return False


def _scope_overlaps_any_conflict(item: ReviewItem, conflict_items: Sequence[ReviewItem]) -> bool:
    return any(other.id != item.id and _scope_overlaps_conflict(item.scope, other.scope) for other in conflict_items)


def _scope_overlaps_conflict(scope: Dict[str, object], conflict_scope: Dict[str, object]) -> bool:
    if str(scope.get("user_id", "user")) != str(conflict_scope.get("user_id", "user")):
        return False
    if str(scope.get("project_id", "default")) != str(conflict_scope.get("project_id", "default")):
        return False
    for key in ("candidate_id", "client_id", "role_id"):
        left = str(scope.get(key, ""))
        right = str(conflict_scope.get(key, ""))
        if left and right and left == right:
            return True

    left_subject = str(scope.get("subject_id") or scope.get("subject") or "")
    right_subject = str(conflict_scope.get("subject_id") or conflict_scope.get("subject") or "")
    if not left_subject or not right_subject or left_subject != right_subject:
        return False

    if left_subject != "user":
        return True

    return _same_specific_relation(scope, conflict_scope)


def _same_specific_relation(scope: Dict[str, object], conflict_scope: Dict[str, object]) -> bool:
    broad_relations = {"", "profile", "context", "memory_context"}
    left_relations = {
        str(scope.get("relation") or ""),
        str(scope.get("relation_type") or ""),
    } - broad_relations
    right_relations = {
        str(conflict_scope.get("relation") or ""),
        str(conflict_scope.get("relation_type") or ""),
    } - broad_relations
    return bool(left_relations.intersection(right_relations))


def _stable_id(prefix: str, parts: Iterable[str]) -> str:
    payload = "||".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return "%s_%s" % (prefix, digest)
