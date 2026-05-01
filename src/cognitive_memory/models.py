from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional, Sequence, Set
from uuid import uuid4

from .source import source_metadata_for


DEFAULT_USER_ID = "user"
DEFAULT_PROJECT_ID = "default"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid4().hex[:12])


def ensure_datetime(value: Optional[Any]) -> datetime:
    if value is None:
        return now_utc()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise TypeError("Unsupported datetime value: %r" % (value,))


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return ensure_datetime(value).isoformat()


def tokenize(text: str) -> Set[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ")
    return set(re.findall(r"[a-z0-9]+", normalized))


def lexical_score(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    text_tokens = tokenize(text)
    if not query_tokens or not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    if overlap == 0:
        return 0.0
    return overlap / float(len(query_tokens))


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def add_days(value: datetime, days: int) -> datetime:
    return ensure_datetime(value) + timedelta(days=days)


@dataclass
class Episode:
    content: str
    actor: str = "user"
    source: str = "chat"
    source_type: str = ""
    source_trust: str = ""
    source_timestamp: Optional[datetime] = None
    source_conflict_policy: str = ""
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    context_id: str = "default"
    sensitivity: str = "low"
    consent_basis: str = "implicit"
    retention_policy: str = "project"
    visibility: str = "user_visible"
    timestamp: datetime = field(default_factory=now_utc)
    id: str = field(default_factory=lambda: make_id("ep"))
    hash: Optional[str] = None
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.timestamp = ensure_datetime(self.timestamp)
        if self.source_timestamp is None:
            self.source_timestamp = self.timestamp
        else:
            self.source_timestamp = ensure_datetime(self.source_timestamp)
        self.created_at = ensure_datetime(self.created_at)
        source_defaults = source_metadata_for(self.source, self.actor)
        if not self.source_type:
            self.source_type = source_defaults["source_type"]
        if not self.source_trust:
            self.source_trust = source_defaults["source_trust"]
        if not self.source_conflict_policy:
            self.source_conflict_policy = source_defaults["source_conflict_policy"]
        if self.hash is None:
            self.hash = str(abs(hash((self.content, iso(self.timestamp), self.user_id, self.project_id))))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = iso(self.timestamp)
        data["source_timestamp"] = iso(self.source_timestamp)
        data["created_at"] = iso(self.created_at)
        return data


@dataclass
class MemoryCandidate:
    claim: str
    type: str = "semantic_fact"
    importance: float = 0.5
    novelty: float = 0.5
    confidence: float = 0.6
    stability: str = "temporary"
    lifespan: str = "until_changed"
    risk_level: str = "low"
    evidence_episode_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    recommended_action: str = "store"
    created_by: str = "extractor"
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: make_id("mc"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.importance = clamp(self.importance)
        self.novelty = clamp(self.novelty)
        self.confidence = clamp(self.confidence)
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        return data


@dataclass
class TemporalFact:
    subject: str
    relation: str
    object: str
    valid_at: datetime
    confidence: float = 0.6
    evidence: List[str] = field(default_factory=list)
    invalid_at: Optional[datetime] = None
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    scope: Dict[str, str] = field(default_factory=dict)
    privacy_policy: str = "normal"
    source_type: str = "unknown"
    source_trust: str = "medium"
    source_timestamp: Optional[datetime] = None
    source_conflict_policy: str = "abstain_on_conflict"
    conflict_with: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: make_id("tf"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.valid_at = ensure_datetime(self.valid_at)
        self.created_at = ensure_datetime(self.created_at)
        if self.source_timestamp is None:
            self.source_timestamp = self.valid_at
        else:
            self.source_timestamp = ensure_datetime(self.source_timestamp)
        if self.invalid_at is not None:
            self.invalid_at = ensure_datetime(self.invalid_at)
        self.confidence = clamp(self.confidence)
        if "user_id" not in self.scope:
            self.scope["user_id"] = DEFAULT_USER_ID
        if "project_id" not in self.scope:
            self.scope["project_id"] = DEFAULT_PROJECT_ID
        self.scope.setdefault("actor_type", "unknown")
        self.scope.setdefault("candidate_id", "")
        self.scope.setdefault("client_id", "")
        self.scope.setdefault("role_id", "")
        self.scope.setdefault("subject_id", self.subject)
        self.scope.setdefault("scope_confidence", 0.0)
        self.scope.setdefault("relation", self.relation)

    @property
    def user_id(self) -> str:
        return self.scope.get("user_id", DEFAULT_USER_ID)

    @property
    def project_id(self) -> str:
        return self.scope.get("project_id", DEFAULT_PROJECT_ID)

    @property
    def actor_type(self) -> str:
        return self.scope.get("actor_type", "unknown")

    @property
    def subject_id(self) -> str:
        return self.scope.get("subject_id", self.subject)

    @property
    def candidate_id(self) -> str:
        return self.scope.get("candidate_id", "")

    @property
    def client_id(self) -> str:
        return self.scope.get("client_id", "")

    @property
    def role_id(self) -> str:
        return self.scope.get("role_id", "")

    @property
    def scope_confidence(self) -> float:
        return float(self.scope.get("scope_confidence", 0.0))

    @property
    def claim_text(self) -> str:
        return "%s %s %s" % (self.subject, self.relation, self.object)

    def is_active(self, at: Optional[datetime] = None) -> bool:
        target = ensure_datetime(at) if at is not None else now_utc()
        if self.valid_at > target:
            return False
        if self.invalid_at is not None and self.invalid_at <= target:
            return False
        return self.privacy_policy not in ("deleted", "do_not_use")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["valid_at"] = iso(self.valid_at)
        data["invalid_at"] = iso(self.invalid_at)
        data["source_timestamp"] = iso(self.source_timestamp)
        data["created_at"] = iso(self.created_at)
        return data


EVENT_RELATION_TYPES = {
    "expressed_preference",
    "stated_requirement",
    "inferred_assumption",
    "contradicted",
    "superseded",
    "applies_to_role",
    "applies_to_client",
    "applies_to_candidate",
    "do_not_contact",
    "do_not_mention",
    "pitch_allowed",
    "pitch_blocked",
    "objection_raised",
    "objection_resolved",
}

REFLECTION_TYPES = {
    "user_preference",
    "candidate_preference",
    "client_requirement",
    "role_requirement",
    "project_pattern",
    "procedural_rule",
    "risk_warning",
    "unresolved_hypothesis",
}

REVIEW_STATUSES = {
    "pending",
    "approved",
    "rejected",
    "needs_more_evidence",
    "deferred",
    "expired",
}


class ReviewStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    DEFERRED = "deferred"
    EXPIRED = "expired"


@dataclass
class EventParticipant:
    entity_id: str
    entity_type: str
    role: str = "subject"
    confidence: float = 0.8
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = clamp(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EventRelation:
    type: str
    subject_id: str
    object_id: str = ""
    relation: str = ""
    value: str = ""
    valid_at: Optional[datetime] = None
    invalid_at: Optional[datetime] = None
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.6

    def __post_init__(self) -> None:
        if self.type not in EVENT_RELATION_TYPES:
            self.type = "expressed_preference"
        if self.valid_at is not None:
            self.valid_at = ensure_datetime(self.valid_at)
        if self.invalid_at is not None:
            self.invalid_at = ensure_datetime(self.invalid_at)
        self.confidence = clamp(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["valid_at"] = iso(self.valid_at)
        data["invalid_at"] = iso(self.invalid_at)
        return data


@dataclass
class EventContext:
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    candidate_id: str = ""
    client_id: str = ""
    role_id: str = ""
    subject_id: str = ""
    conversation_id: str = "default"
    source_type: str = "unknown"
    source_trust: str = "medium"
    timestamp: datetime = field(default_factory=now_utc)
    confidence: float = 0.6

    def __post_init__(self) -> None:
        self.timestamp = ensure_datetime(self.timestamp)
        self.confidence = clamp(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = iso(self.timestamp)
        return data


@dataclass
class MemoryEvent:
    event_type: str
    content: str
    timestamp: datetime
    participants: List[EventParticipant] = field(default_factory=list)
    relations: List[EventRelation] = field(default_factory=list)
    context: EventContext = field(default_factory=EventContext)
    evidence_episode_ids: List[str] = field(default_factory=list)
    confidence: float = 0.6
    status: str = "active"
    source_fact_id: str = ""
    id: str = field(default_factory=lambda: make_id("me"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.timestamp = ensure_datetime(self.timestamp)
        self.created_at = ensure_datetime(self.created_at)
        self.confidence = clamp(self.confidence)

    @property
    def claim_text(self) -> str:
        return self.content

    def is_active(self, at: Optional[datetime] = None) -> bool:
        target = ensure_datetime(at) if at is not None else now_utc()
        if self.timestamp > target:
            return False
        return self.status == "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "content": self.content,
            "timestamp": iso(self.timestamp),
            "participants": [item.to_dict() for item in self.participants],
            "relations": [item.to_dict() for item in self.relations],
            "context": self.context.to_dict(),
            "evidence_episode_ids": list(self.evidence_episode_ids),
            "confidence": self.confidence,
            "status": self.status,
            "source_fact_id": self.source_fact_id,
            "id": self.id,
            "created_at": iso(self.created_at),
        }


@dataclass
class Reflection:
    claim: str
    confidence: float
    supporting_evidence: List[str]
    counter_evidence: List[str] = field(default_factory=list)
    scope: str = "user"
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    actor_type: str = "unknown"
    candidate_id: str = ""
    client_id: str = ""
    role_id: str = ""
    subject_id: str = ""
    relation_type: str = ""
    scope_confidence: float = 0.0
    reflection_type: str = "unresolved_hypothesis"
    decay_rate: float = 0.05
    decay_score: float = 0.0
    last_reinforced_at: Optional[datetime] = None
    review_after: Optional[datetime] = None
    archived: bool = False
    last_reviewed: datetime = field(default_factory=now_utc)
    next_review_at: datetime = field(default_factory=lambda: add_days(now_utc(), 30))
    status: str = "hypothesis"
    id: str = field(default_factory=lambda: make_id("rf"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.confidence = clamp(self.confidence)
        self.scope_confidence = clamp(self.scope_confidence)
        self.decay_rate = clamp(self.decay_rate)
        self.decay_score = clamp(self.decay_score)
        if self.reflection_type not in REFLECTION_TYPES:
            self.reflection_type = "unresolved_hypothesis"
        if not self.subject_id:
            self.subject_id = self.candidate_id or self.client_id or self.role_id or self.user_id
        if self.last_reinforced_at is not None:
            self.last_reinforced_at = ensure_datetime(self.last_reinforced_at)
        if self.review_after is not None:
            self.review_after = ensure_datetime(self.review_after)
        self.last_reviewed = ensure_datetime(self.last_reviewed)
        self.next_review_at = ensure_datetime(self.next_review_at)
        self.created_at = ensure_datetime(self.created_at)

    @property
    def claim_text(self) -> str:
        return self.claim

    def is_active(self) -> bool:
        return not self.archived and self.status in ("hypothesis", "accepted")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["last_reinforced_at"] = iso(self.last_reinforced_at)
        data["review_after"] = iso(self.review_after)
        data["last_reviewed"] = iso(self.last_reviewed)
        data["next_review_at"] = iso(self.next_review_at)
        data["created_at"] = iso(self.created_at)
        return data


@dataclass
class ConsolidatedMemory:
    claim: str
    memory_type: str = "reflection"
    scope: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reflection_type: str = "unresolved_hypothesis"
    status: str = "proposed"
    decay_score: float = 0.0
    last_reinforced_at: Optional[datetime] = None
    review_after: Optional[datetime] = None
    archived: bool = False
    id: str = field(default_factory=lambda: make_id("cm"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.confidence = clamp(self.confidence)
        self.decay_score = clamp(self.decay_score)
        if self.reflection_type not in REFLECTION_TYPES:
            self.reflection_type = "unresolved_hypothesis"
        if self.last_reinforced_at is not None:
            self.last_reinforced_at = ensure_datetime(self.last_reinforced_at)
        if self.review_after is not None:
            self.review_after = ensure_datetime(self.review_after)
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["last_reinforced_at"] = iso(self.last_reinforced_at)
        data["review_after"] = iso(self.review_after)
        data["created_at"] = iso(self.created_at)
        return data


@dataclass
class ConsolidationCandidate:
    claim: str
    memory_type: str = "reflection"
    scope: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source_ids: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    proposed_memory: Optional[ConsolidatedMemory] = None
    id: str = field(default_factory=lambda: make_id("cc"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.confidence = clamp(self.confidence)
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        data["proposed_memory"] = self.proposed_memory.to_dict() if self.proposed_memory is not None else None
        return data


@dataclass
class ConsolidationDecision:
    candidate_id: str
    action: str
    reason: str
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    scope: Dict[str, Any] = field(default_factory=dict)
    memory_type: str = "reflection"
    review_required: bool = False
    target_id: str = ""
    proposed_memory: Optional[ConsolidatedMemory] = None
    decay_metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: make_id("cd"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.confidence = clamp(self.confidence)
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        data["proposed_memory"] = self.proposed_memory.to_dict() if self.proposed_memory is not None else None
        data["decay_metadata"] = _json_ready_dict(self.decay_metadata)
        return data


@dataclass
class ConsolidationRun:
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    status: str = "dry_run"
    candidates: List[ConsolidationCandidate] = field(default_factory=list)
    decisions: List[ConsolidationDecision] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    audit_log: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: make_id("cr"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "project_id": self.project_id,
            "status": self.status,
            "candidates": [item.to_dict() for item in self.candidates],
            "decisions": [item.to_dict() for item in self.decisions],
            "summary": _json_ready_dict(self.summary),
            "audit_log": list(self.audit_log),
            "id": self.id,
            "created_at": iso(self.created_at),
        }


@dataclass
class ReviewItem:
    consolidation_run_id: str
    consolidation_decision_id: str
    candidate_id: str
    proposed_action: str
    reason: str
    risk_level: str
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    scope: Dict[str, Any] = field(default_factory=dict)
    memory_type: str = "reflection"
    proposed_memory_id: str = ""
    proposed_memory_summary: str = ""
    review_required: bool = False
    status: str = ReviewStatus.PENDING
    id: str = field(default_factory=lambda: make_id("ri"))
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        if self.status not in REVIEW_STATUSES:
            self.status = ReviewStatus.PENDING
        if self.risk_level not in ("low", "medium", "high"):
            self.risk_level = "medium"
        self.created_at = ensure_datetime(self.created_at)
        self.updated_at = ensure_datetime(self.updated_at)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        data["updated_at"] = iso(self.updated_at)
        return data


@dataclass
class ReviewDecision:
    review_item_id: str
    status: str
    reason: str
    reviewer_id: str = ""
    decision_confidence: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    scope: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"
    proposed_action: str = ""
    decision_timestamp: datetime = field(default_factory=now_utc)
    id: str = field(default_factory=lambda: make_id("rd"))

    def __post_init__(self) -> None:
        if self.status not in REVIEW_STATUSES:
            self.status = ReviewStatus.PENDING
        if self.risk_level not in ("low", "medium", "high"):
            self.risk_level = "medium"
        self.decision_confidence = clamp(self.decision_confidence)
        self.decision_timestamp = ensure_datetime(self.decision_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["decision_timestamp"] = iso(self.decision_timestamp)
        return data


@dataclass
class ReviewQueue:
    consolidation_run_id: str
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    mode: str = "review_required"
    items: List[ReviewItem] = field(default_factory=list)
    decisions: List[ReviewDecision] = field(default_factory=list)
    status: str = ReviewStatus.PENDING
    summary: Dict[str, Any] = field(default_factory=dict)
    audit_log: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: make_id("rq"))
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        if self.status not in REVIEW_STATUSES:
            self.status = ReviewStatus.PENDING
        self.created_at = ensure_datetime(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consolidation_run_id": self.consolidation_run_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "items": [item.to_dict() for item in self.items],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "status": self.status,
            "summary": _json_ready_dict(self.summary),
            "audit_log": list(self.audit_log),
            "id": self.id,
            "created_at": iso(self.created_at),
        }


def _json_ready_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    ready: Dict[str, Any] = {}
    for key, item in value.items():
        if hasattr(item, "isoformat"):
            ready[key] = iso(item)
        elif isinstance(item, dict):
            ready[key] = _json_ready_dict(item)
        elif isinstance(item, (list, tuple)):
            ready[key] = [iso(entry) if hasattr(entry, "isoformat") else entry for entry in item]
        else:
            ready[key] = item
    return ready


@dataclass
class RetrievalMemoryPolicy:
    allow_sensitive: bool = False
    allow_reflections: bool = True
    require_provenance: bool = True
    exclude_invalidated: bool = True
    exclude_do_not_use: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalRequest:
    query: str
    user_id: str = DEFAULT_USER_ID
    project_id: str = DEFAULT_PROJECT_ID
    task_type: str = "general"
    time_scope: str = "current"
    as_of: Optional[datetime] = None
    memory_policy: RetrievalMemoryPolicy = field(default_factory=RetrievalMemoryPolicy)
    top_k: int = 5
    min_score: float = 0.05

    def __post_init__(self) -> None:
        if self.as_of is not None:
            self.as_of = ensure_datetime(self.as_of)


@dataclass
class ExcludedMemory:
    id: str
    reason: str
    memory_type: str
    claim: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SelectedMemory:
    id: str
    memory_type: str
    claim: str
    score: float
    confidence: float
    evidence: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    selected_memories: List[SelectedMemory] = field(default_factory=list)
    excluded_memories: List[ExcludedMemory] = field(default_factory=list)
    provenance: List[str] = field(default_factory=list)
    confidence: float = 0.0
    abstain_recommended: bool = False
    abstain_reason: str = ""
    retrieval_trace: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def answer_text(self) -> str:
        if self.abstain_recommended or not self.selected_memories:
            return "ABSTAIN"
        return " | ".join(memory.claim for memory in self.selected_memories)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_memories": [item.to_dict() for item in self.selected_memories],
            "excluded_memories": [item.to_dict() for item in self.excluded_memories],
            "provenance": list(self.provenance),
            "confidence": self.confidence,
            "abstain_recommended": self.abstain_recommended,
            "abstain_reason": self.abstain_reason,
            "retrieval_trace": self.retrieval_trace,
            "metadata": dict(self.metadata),
        }


def evidence_ids_from_items(items: Sequence[Any]) -> List[str]:
    evidence: List[str] = []
    for item in items:
        if isinstance(item, TemporalFact):
            evidence.extend(item.evidence)
        elif isinstance(item, Reflection):
            evidence.extend(item.supporting_evidence)
    return sorted(set(evidence))
