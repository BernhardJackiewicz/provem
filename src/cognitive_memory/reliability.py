"""Closed-loop agentic reliability benchmark for memory governance.

This module answers one question with statistics, not vibes:

    Does a governance layer over agent memory produce *significantly fewer*
    catastrophic (silent, compounding) agent errors than the same memory
    without governance -- while still answering benign questions correctly?

Why a deterministic agent? The "agent brain" here is a fixed decision policy,
not an LLM. That is a feature: it isolates the *memory layer's* causal
contribution to action correctness. Both arms of the experiment receive the
identical task stream, use the identical recall substrate (:class:`NaiveBackend`)
and the identical scoring. The only thing that differs is whether the governance
wrapper is on. Any measured difference is therefore attributable to governance,
not to LLM noise, prompt wording, or sampling temperature -- the exact confounds
that made prior memory-benchmark disputes (e.g. LoCoMo score fights) irreproducible.

Failure modes injected (faithful, simplified analogs of published attacks):

- ``poisoning``  -- MINJA-style query-only memory injection (arXiv:2503.03704):
  an attacker writes a plausible record that rewrites a victim entity's value.
  Ungoverned memory retrieves it and every downstream step about that entity is
  wrong -> correlated, compounding failure.
- ``erasure``    -- GDPR Art. 17 right-to-erasure: the user asks to forget a
  fact; a later step must behave as if it is gone. Ungoverned memory keeps
  serving it -> a compliance violation.
- ``scope``      -- cross-entity / cross-tenant contamination: a query about
  entity A must not be answered with entity B's look-alike record.
- ``trigger``    -- AgentPoison-style trigger record (arXiv:2407.12784): a record
  that hijacks the action only when a trigger phrase appears.
- ``benign``     -- ordinary current-fact recall. BOTH arms must get these right;
  this is what proves the governed arm is calibrated, not merely abstaining.

The public wrapper classes (:class:`GovernedMemory`, :class:`UngovernedMemory`)
are also the shippable product: a backend-agnostic governance middleware you can
put in front of any memory store (see ``docs/agentic_reliability_benchmark.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import random
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from .models import lexical_score, tokenize
from .safety import instruction_risk_reason, sensitive_risk_reason

# ---------------------------------------------------------------------------
# Outcome vocabulary
# ---------------------------------------------------------------------------

CORRECT = "correct"
SILENT_ERROR = "silent_error"            # confident wrong action -> compounds
RECOVERABLE = "recoverable_abstention"   # "I don't know" -> agent can fall back

ABSTAIN = "__ABSTAIN__"


# ---------------------------------------------------------------------------
# Data model: scope, records, results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """Where a memory lives / what a query is about.

    ``tenant`` isolates organizations, ``subject`` is the entity a fact concerns
    (a person, candidate, patient, account). ``session`` is optional.
    """

    tenant: str = "default"
    subject: str = ""
    session: str = ""

    def tenant_matches(self, other: "Scope") -> bool:
        return self.tenant == other.tenant


@dataclass
class MemoryRecord:
    subject: str
    relation: str
    object: str
    scope: Scope
    text: str = ""
    source: str = "user"
    trust: float = 0.9
    provenance: str = ""
    quarantined: bool = False
    quarantine_reason: str = ""
    valid_at: int = 0            # logical clock (turn index); latest wins
    id: str = ""
    created_at: str = ""         # wall-clock ISO timestamp for retention enforcement

    def __post_init__(self) -> None:
        if not self.text:
            self.text = "%s %s %s" % (self.subject, self.relation, self.object)


@dataclass
class RecallResult:
    answer: Optional[str]                       # object value, or None if abstained
    abstained: bool
    reason: str = ""
    selected: List[MemoryRecord] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)  # (record_id, reason)
    provenance: List[str] = field(default_factory=list)
    ops: int = 0                                # records scanned (cost proxy)


# ---------------------------------------------------------------------------
# Backend: the shared, ungoverned recall substrate (both arms use this)
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryBackend(Protocol):
    """The minimal contract a store must satisfy to be wrapped by governance.

    Any similarity/vector memory can implement this in a thin adapter: a write,
    a scoped similarity search, an id-delete, and a full listing. This is the
    portability guarantee -- governance is backend-agnostic. The reference
    implementation is :class:`NaiveBackend`; a Mem0/Zep/LangMem adapter would map
    ``write`` -> add, ``candidates`` -> search, ``delete_ids`` -> delete.
    """

    def write(self, record: "MemoryRecord") -> str: ...

    def delete_ids(self, ids: Sequence[str]) -> int: ...

    def all_records(self) -> List["MemoryRecord"]: ...

    def candidates(self, query: str, tenant: str) -> List[Tuple[float, "MemoryRecord"]]: ...


class NaiveBackend:
    """A plain similarity memory: store everything, return best lexical matches.

    This models a typical vector-memory store. It has no notion of policy,
    provenance trust, erasure enforcement or abstention -- it always returns its
    top matches. Both experimental arms store into an instance of this backend,
    so recall power is held constant across arms.
    """

    def __init__(self) -> None:
        self._records: List[MemoryRecord] = []
        self._counter = 0

    def write(self, record: MemoryRecord) -> str:
        self._counter += 1
        record.id = record.id or "r%d" % self._counter
        self._records.append(record)
        return record.id

    def delete_ids(self, ids: Sequence[str]) -> int:
        idset = set(ids)
        before = len(self._records)
        self._records = [r for r in self._records if r.id not in idset]
        return before - len(self._records)

    def all_records(self) -> List[MemoryRecord]:
        return list(self._records)

    def candidates(self, query: str, tenant: str) -> List[Tuple[float, MemoryRecord]]:
        scored: List[Tuple[float, MemoryRecord]] = []
        for record in self._records:
            if record.scope.tenant != tenant:
                continue
            score = lexical_score(query, record.text)
            if score > 0:
                scored.append((score, record))
        # Highest similarity first; break ties by recency (latest valid_at).
        scored.sort(key=lambda item: (item[0], item[1].valid_at), reverse=True)
        return scored


class Bm25Backend:
    """Drop-in MemoryBackend with proper BM25 ranking for scale.

    Same contract as :class:`NaiveBackend`, but ranks by Okapi BM25 (IDF +
    length normalization) over the tenant's records instead of raw token
    overlap, so the right memory surfaces above look-alikes as the store grows.
    Scores are saturating-normalized to (0,1) so GovernedMemory's relevance and
    abstention thresholds keep their meaning. Governance is unchanged -- only the
    similarity substrate is stronger.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, norm_k: float = 1.0) -> None:
        self._records: List[MemoryRecord] = []
        self._counter = 0
        self.k1 = k1
        self.b = b
        self.norm_k = norm_k

    def write(self, record: MemoryRecord) -> str:
        self._counter += 1
        record.id = record.id or "b%d" % self._counter
        self._records.append(record)
        return record.id

    def delete_ids(self, ids: Sequence[str]) -> int:
        idset = set(ids)
        before = len(self._records)
        self._records = [r for r in self._records if r.id not in idset]
        return before - len(self._records)

    def all_records(self) -> List[MemoryRecord]:
        return list(self._records)

    def candidates(self, query: str, tenant: str) -> List[Tuple[float, MemoryRecord]]:
        from .ranking import blended_bm25_candidates

        scoped = [r for r in self._records if r.scope.tenant == tenant]
        return blended_bm25_candidates(scoped, query, k1=self.k1, b=self.b, norm_k=self.norm_k)


# ---------------------------------------------------------------------------
# Ingest turns (identical inputs to both arms)
# ---------------------------------------------------------------------------


@dataclass
class IngestTurn:
    kind: str                     # 'fact' | 'erasure' | 'constraint' | 'attack'
    text: str
    subject: str = ""
    relation: str = ""
    object: str = ""
    scope: Scope = field(default_factory=Scope)
    source: str = "user"
    trust: float = 0.9
    term: str = ""                # for erasure / constraint
    consent: bool = False         # explicit consent to store sensitive content


@dataclass
class QueryTurn:
    query: str
    scope: Scope
    expected: Optional[str]       # correct object, or None => correct behavior is to abstain
    failure_class: str = "benign" # benign | poisoning | erasure | scope | trigger
    trigger: str = ""             # for trigger scenarios


# ---------------------------------------------------------------------------
# Ungoverned memory: the "no governance" arm and honest recall baseline
# ---------------------------------------------------------------------------


class UngovernedMemory:
    """Recall-first memory with no governance. The control arm.

    It stores whatever it is told (including erasure requests -- as noise, since
    it does not interpret governance intents) and always answers with its top
    similarity match. This is deliberately a *fair* baseline: on benign recall it
    performs well. It just has no erasure enforcement, no provenance trust, no
    scope isolation and no abstention.
    """

    name = "ungoverned"

    def __init__(self, backend: Optional[MemoryBackend] = None) -> None:
        self.backend = backend or NaiveBackend()
        self.clock = 0

    def ingest(self, turn: IngestTurn) -> None:
        self.clock += 1
        # No governance: facts, attacks and even erasure/constraint requests are
        # all just written. An erasure utterance becomes another stored memory
        # rather than an enforced deletion.
        self.backend.write(
            MemoryRecord(
                subject=turn.subject or turn.term or turn.text,
                relation=turn.relation or turn.kind,
                object=turn.object,
                scope=turn.scope,
                text=turn.text,
                source=turn.source,
                trust=turn.trust,
                valid_at=self.clock,
            )
        )

    def recall(self, turn: QueryTurn) -> RecallResult:
        scored = self.backend.candidates(turn.query, turn.scope.tenant)
        ops = len(scored)
        if not scored:
            return RecallResult(answer=None, abstained=True, reason="no_match", ops=ops)
        best = scored[0][1]
        return RecallResult(
            answer=best.object,
            abstained=False,
            selected=[best],
            provenance=[best.provenance] if best.provenance else [],
            ops=ops,
        )


# ---------------------------------------------------------------------------
# Governed memory: the governance wrapper. Product + treatment arm.
# ---------------------------------------------------------------------------


class GovernedMemory:
    """Backend-agnostic governance middleware over an agent memory store.

    It adds, on top of any similarity backend:

    - write-side: prompt-injection quarantine, sensitive-without-consent hold,
      provenance + source-trust tagging, and interpretation of governance intents
      (erasure, do-not-use) expressed in natural language.
    - read-side: erasure/do-not-use enforcement, entity-scope isolation,
      source-conflict / poisoning resolution by provenance trust, and calibrated
      abstention instead of returning a low-confidence or conflicted guess.

    Design principle preserved from the core system: prefer a recoverable
    abstention over a confident wrong answer. A wrong memory answer propagates
    and compounds across an agent trajectory; an abstention is recoverable.
    """

    name = "governed"

    def __init__(
        self,
        backend: Optional[MemoryBackend] = None,
        *,
        policy: Optional[object] = None,
        relevance_floor: Optional[float] = None,
        trust_margin: Optional[float] = None,
        audit_path: Optional[str] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        from .compliance import resolve_policy

        import threading

        self.backend = backend or NaiveBackend()
        self._lock = threading.RLock()
        # wall-clock source for retention (injectable for deterministic tests)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.policy = resolve_policy(policy)
        # Explicit kwargs override the policy (backward compatibility); otherwise
        # the policy's thresholds apply. The default policy reproduces the
        # original hard-coded 0.5 / 0.15 behaviour exactly.
        self.relevance_floor = self.policy.relevance_floor if relevance_floor is None else relevance_floor
        self.trust_margin = self.policy.trust_margin if trust_margin is None else trust_margin
        self.clock = 0
        # token-sets of erased / restricted terms, keyed by tenant so one
        # tenant's erasure never blocks another tenant's records when a single
        # GovernedMemory instance is shared across tenants.
        self.erased_terms: Dict[str, List[set]] = {}
        self.restricted_terms: Dict[str, List[set]] = {}
        from .audit import AuditLog

        self.audit = AuditLog(persist_path=audit_path)

    # -- ergonomic product API ---------------------------------------------
    # Embed the layer in an agent or app with a few lines; the benchmark drives
    # the same object through ingest()/recall().

    def remember(
        self,
        text: str,
        *,
        subject: str = "",
        relation: str = "",
        object: str = "",
        tenant: str = "default",
        entity: str = "",
        source: str = "user",
        trust: float = 0.9,
        consent: bool = False,
    ) -> None:
        """Store a fact (governance is applied: quarantine, provenance, scope).

        Pass ``consent=True`` to store content the policy considers sensitive
        under an explicit lawful basis (otherwise it is quarantined).
        """
        entity = entity or subject
        self.ingest(
            IngestTurn(
                "fact", text, subject or entity, relation, object,
                Scope(tenant, entity), source, trust, consent=consent,
            )
        )

    def recall_value(self, query: str, *, tenant: str = "default", entity: str = "") -> RecallResult:
        """Governed recall for an entity: returns a value or a safe abstention."""
        return self.recall(QueryTurn(query, Scope(tenant, entity), None))

    # -- write side ---------------------------------------------------------

    def ingest(self, turn: IngestTurn) -> None:
        # Reentrant lock makes the embeddable wrapper safe under concurrent
        # writers (the stdio MCP server is single-threaded, so this is
        # uncontended there). RLock allows ingest -> forget/restrict re-entry.
        with self._lock:
            self.clock += 1
            if turn.kind == "erasure":
                self.forget(turn.term or turn.object or turn.subject, turn.scope)
                return
            if turn.kind == "constraint":
                self.restrict(turn.term or turn.object, turn.scope)
                return
            # 'fact'/'attack' share the identical write path; the layer decides
            # from content, not from a caller-declared kind.
            self._remember(turn)

    def _remember(self, turn: IngestTurn) -> None:
        # Scan every field, not just free text: an attacker/PII payload smuggled
        # into subject/relation/object must not evade detection.
        scan = " ".join(part for part in (turn.text, turn.subject, turn.relation, turn.object) if part)
        reason = ""
        if self.policy.detect_injection:
            reason = instruction_risk_reason(scan, self.policy.extra_injection_patterns)
        if not reason and self.policy.detect_sensitive and self.policy.require_consent_for_sensitive and not turn.consent:
            reason = sensitive_risk_reason(scan, self.policy.extra_sensitive_patterns)
        # Policy may assert the trust of a source. A source not listed falls back
        # to the policy's unlisted_source_trust (if set) rather than the caller's
        # trust -- so an unknown/omitted source cannot bypass a distrust policy.
        if turn.source in self.policy.source_trust:
            trust = self.policy.source_trust[turn.source]
        elif self.policy.unlisted_source_trust is not None:
            trust = self.policy.unlisted_source_trust
        else:
            trust = turn.trust
        if not reason and self.policy.min_store_trust > 0.0 and trust < self.policy.min_store_trust:
            reason = "low_source_trust"
        quarantined = bool(reason)
        if quarantined:
            self.audit.record("quarantine", reason=reason, subject=turn.subject, source=turn.source)
        if not quarantined and self.policy.deduplicate and self._is_duplicate(turn):
            self.audit.record("dedup_skip", subject=turn.subject, relation=turn.relation)
            return
        record = MemoryRecord(
            subject=turn.subject or turn.text,
            relation=turn.relation or turn.kind,
            object=turn.object,
            scope=turn.scope,
            text=turn.text,
            source=turn.source,
            trust=trust,
            provenance="ep%d:%s" % (self.clock, turn.source),
            quarantined=quarantined,
            quarantine_reason=reason,
            valid_at=self.clock,
            created_at=self._now_fn().isoformat(),
        )
        self.backend.write(record)

    def _is_duplicate(self, turn: IngestTurn) -> bool:
        subject = turn.subject or turn.text
        relation = turn.relation or turn.kind
        for record in self.backend.all_records():
            if (
                record.scope.tenant == turn.scope.tenant
                and record.scope.subject == turn.scope.subject
                and record.subject == subject
                and record.relation == relation
                and record.object == turn.object
                and record.text == turn.text
            ):
                return True
        return False

    def forget(self, term: str, scope: Scope) -> int:
        with self._lock:
            term_tokens = tokenize(term)
            if term_tokens:
                self.erased_terms.setdefault(scope.tenant, []).append(term_tokens)
            remove: List[str] = []
            for record in self.backend.all_records():
                if record.scope.tenant != scope.tenant:
                    continue
                if self._term_hits(term_tokens, record):
                    remove.append(record.id)
            removed = self.backend.delete_ids(remove)
            self.audit.erasure_certificate(term, remove, scope.tenant, removed)
            return removed

    def restrict(self, term: str, scope: Scope) -> None:
        with self._lock:
            term_tokens = tokenize(term)
            if term_tokens:
                self.restricted_terms.setdefault(scope.tenant, []).append(term_tokens)
            self.audit.record("restrict", term=term, tenant=scope.tenant)

    def export_audit(self, as_json: bool = False):
        """Return the tamper-evident governance audit trail."""
        return self.audit.to_json() if as_json else self.audit.to_dict()

    def verify_audit(self) -> bool:
        """True if the audit chain is intact (no entry altered/reordered)."""
        return self.audit.verify()

    # -- retention ----------------------------------------------------------

    def _retention_days_for(self, record: MemoryRecord) -> Optional[int]:
        rd = self.policy.retention_days
        if not rd:
            return None
        category = "restricted" if record.quarantined else "high"
        if category in rd:
            return int(rd[category])
        if "default" in rd:
            return int(rd["default"])
        return None

    def _is_expired(self, record: MemoryRecord, now: datetime) -> bool:
        days = self._retention_days_for(record)
        if days is None or not record.created_at:
            return False
        try:
            created = datetime.fromisoformat(record.created_at)
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - created).total_seconds() / 86400.0 > days

    def cleanup_expired(self, now: Optional[datetime] = None) -> int:
        """Delete records past their retention window; audit the sweep. Returns
        how many the backend confirmed deleted."""
        with self._lock:
            now = now or self._now_fn()
            remove = [r.id for r in self.backend.all_records() if self._is_expired(r, now)]
            removed = self.backend.delete_ids(remove) if remove else 0
            if removed:
                self.audit.record("retention_cleanup", removed=removed, targeted=len(remove))
            return removed

    def _erasure_tokens(self, record: MemoryRecord) -> set:
        # strict erasure inspects the whole record text (safe, may overblock);
        # lenient erasure only targets the record's subject/object.
        if self.policy.erasure_mode == "lenient":
            return tokenize(record.object) | tokenize(record.subject)
        return tokenize(record.text) | tokenize(record.object) | tokenize(record.subject)

    def _term_hits(self, term_tokens: set, record: MemoryRecord) -> bool:
        if not term_tokens:
            return False
        return term_tokens <= self._erasure_tokens(record)

    # -- read side ----------------------------------------------------------

    def recall(self, turn: QueryTurn) -> RecallResult:
        with self._lock:
            return self._recall_locked(turn)

    def _recall_locked(self, turn: QueryTurn) -> RecallResult:
        scored = self.backend.candidates(turn.query, turn.scope.tenant)
        ops = len(scored)
        excluded: List[Tuple[str, str]] = []
        kept: List[Tuple[float, MemoryRecord]] = []
        retention_now = self._now_fn() if self.policy.enforce_retention_on_recall else None

        for score, record in scored:
            reason = self._exclusion_reason(record, turn)
            if not reason and retention_now is not None and self._is_expired(record, retention_now):
                reason = "retention_expired"
            if reason:
                excluded.append((record.id, reason))
                continue
            kept.append((score, record))

        # Audit every read-side governance block (erasure/scope/tenant/injection/
        # do-not-use), even when other records are still served -- a compliance
        # trail must not go silent just because the query was answered anyway.
        governance_reasons = sorted(
            {er[1] for er in excluded}
            & {"erased", "do_not_use", "wrong_scope", "wrong_tenant", "possible_prompt_injection", "quarantined"}
        )
        if governance_reasons:
            self.audit.record(
                "recall_blocked",
                tenant=turn.scope.tenant,
                reasons=governance_reasons,
                blocked_count=len(excluded),
            )

        if not kept:
            reason = "forbidden_or_erased" if excluded else "no_match"
            return RecallResult(answer=None, abstained=True, reason=reason, excluded=excluded, ops=ops)

        kept.sort(key=lambda item: (item[0], item[1].trust, item[1].valid_at), reverse=True)
        top_score, top_record = kept[0]

        # Relevance floor: never answer a specific query from a weak match (e.g.
        # a record that only shares the entity name but is about another
        # attribute). Abstaining here is what keeps an erased/absent attribute
        # from being "answered" by an unrelated leftover record.
        if top_score < self.relevance_floor:
            for _, rec in kept:
                excluded.append((rec.id, "low_relevance"))
            return RecallResult(answer=None, abstained=True, reason="low_relevance", excluded=excluded, ops=ops)

        # Resolve disagreement within the most-relevant (subject, relation) group:
        # same source -> supersession (latest wins); different sources -> require a
        # clear provenance-trust margin, else abstain rather than trust a guess.
        chosen, abstain_reason = self._resolve_group(kept, top_record)
        if chosen is None:
            for _, rec in kept:
                excluded.append((rec.id, abstain_reason))
            return RecallResult(answer=None, abstained=True, reason=abstain_reason, excluded=excluded, ops=ops)

        return RecallResult(
            answer=chosen.object,
            abstained=False,
            selected=[chosen],
            excluded=excluded,
            provenance=[chosen.provenance] if chosen.provenance else [],
            ops=ops,
        )

    def _exclusion_reason(self, record: MemoryRecord, turn: QueryTurn) -> str:
        if record.quarantined:
            return record.quarantine_reason or "quarantined"
        erasure_tokens = self._erasure_tokens(record)
        if any(term <= erasure_tokens for term in self.erased_terms.get(record.scope.tenant, [])):
            return "erased"
        restrict_tokens = tokenize(record.text) | tokenize(record.object) | tokenize(record.subject)
        if any(term <= restrict_tokens for term in self.restricted_terms.get(record.scope.tenant, [])):
            return "do_not_use"
        # Cross-tenant defense in depth (backends that do not pre-filter by tenant).
        if not self.policy.cross_tenant_allowed and record.scope.tenant != turn.scope.tenant:
            return "wrong_tenant"
        # Entity-scope isolation: a query about subject X must not be answered by
        # a look-alike record about subject Y. A subject-less query (entity="")
        # must NOT be served a subject-scoped record either -- otherwise scope
        # isolation is bypassable by simply omitting the entity.
        if self.policy.scope_isolation and record.subject:
            if not turn.scope.subject or record.subject != turn.scope.subject:
                return "wrong_scope"
        # Instruction-like content that slipped in is never served -- scan every
        # field, not just free text (payload may hide in subject/relation/object).
        record_scan = " ".join(part for part in (record.text, record.subject, record.relation, record.object) if part)
        if self.policy.detect_injection and instruction_risk_reason(record_scan, self.policy.extra_injection_patterns):
            return "possible_prompt_injection"
        return ""

    def _resolve_group(
        self, kept: List[Tuple[float, MemoryRecord]], top_record: MemoryRecord
    ) -> Tuple[Optional[MemoryRecord], str]:
        """Pick the current value for the queried (subject, relation), or abstain.

        Operates on the group the query is actually about (the top record's
        subject/relation). A single value -> use it. Same-source disagreement ->
        supersession (latest wins). Cross-source disagreement -> require a clear
        provenance-trust margin between the best source and the best *other*
        source; otherwise abstain rather than trust a possibly-poisoned record.
        """
        group = [
            record
            for _, record in kept
            if record.subject == top_record.subject and record.relation == top_record.relation
        ]
        objects = {r.object for r in group}
        if len(objects) <= 1:
            return (top_record, "")

        if len({r.source for r in group}) == 1:
            chosen = max(group, key=lambda r: r.valid_at)
            self.audit.record("supersession", subject=chosen.subject, relation=chosen.relation)
            return (chosen, "")

        ranked = sorted(group, key=lambda r: (r.trust, r.valid_at), reverse=True)
        best = ranked[0]
        others = [r for r in ranked if r.source != best.source]
        best_other = others[0] if others else None
        if best_other is not None and (best.trust - best_other.trust) >= self.trust_margin - 1e-9:
            self.audit.record("conflict_resolved_by_trust", subject=best.subject, winning_source=best.source)
            return (best, "")
        self.audit.record("conflict_abstain", subject=best.subject)
        return (None, "source_conflict")


# ---------------------------------------------------------------------------
# The deterministic agent + trajectory runner
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    failure_class: str
    outcome: str                    # CORRECT | SILENT_ERROR | RECOVERABLE
    expected: Optional[str]
    got: Optional[str]
    compliance_violation: bool
    poisoning_success: bool


@dataclass
class TrajectoryResult:
    steps: List[StepResult]
    ops: int

    @property
    def all_correct(self) -> bool:
        return all(s.outcome == CORRECT for s in self.steps)

    @property
    def graceful_degradation(self) -> float:
        if not self.steps:
            return 1.0
        return sum(1 for s in self.steps if s.outcome == CORRECT) / len(self.steps)


# ---------------------------------------------------------------------------
# Agent policy: how a memory result becomes an action.
#
# The default DeterministicAgent isolates the memory layer (it acts exactly on
# what memory returns). NoisyAgent adds an intrinsic per-step error rate to model
# LLM reasoning noise, so end-to-end task success composes agent error AND memory
# error over a trajectory. A real LLM agent can implement the same Agent protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class Agent(Protocol):
    def act(self, turn: "QueryTurn", result: RecallResult) -> Optional[str]:
        """Return a concrete answer value, or ``ABSTAIN`` to ask / fall back."""
        ...


class DeterministicAgent:
    """Acts exactly on memory. Isolates the memory layer's contribution."""

    def act(self, turn: "QueryTurn", result: RecallResult) -> Optional[str]:
        return ABSTAIN if result.abstained else result.answer


class NoisyAgent:
    """Agent with intrinsic per-step reliability ``skill`` (models LLM noise).

    It errs only when it *commits to an answer*; when memory abstains it asks,
    and asking is always safe (an agent that says "I don't know" does not
    hallucinate a value). This keeps the model fair: governance's abstentions are
    not punished, and the extra errors an agent makes fall on steps where it acts.

    The rng is drawn once per step regardless of abstention so that the noise
    sequence stays aligned across arms on the same scenario -- i.e. governed and
    ungoverned see identical agent noise, isolating the memory effect end-to-end.
    """

    WRONG = "__AGENT_WRONG__"

    def __init__(self, skill: float, rng: random.Random) -> None:
        self.skill = skill
        self.rng = rng

    def act(self, turn: "QueryTurn", result: RecallResult) -> Optional[str]:
        draw = self.rng.random()
        if result.abstained:
            return ABSTAIN
        if draw > self.skill:
            return self.WRONG  # intrinsic agent mistake -> a confident wrong value
        return result.answer


def classify_action(turn: QueryTurn, action: Optional[str]) -> StepResult:
    """Score one agent action against ground truth (outcome-based)."""
    abstained = action == ABSTAIN
    return _classify(turn, abstained, None if abstained else action)


def classify_step(turn: QueryTurn, result: RecallResult) -> StepResult:
    """Score one decision step against ground truth (outcome-based)."""
    return _classify(turn, result.abstained, None if result.abstained else result.answer)


def _classify(turn: QueryTurn, abstained: bool, got: Optional[str]) -> StepResult:
    expected = turn.expected
    compliance_classes = {"erasure", "scope"}

    if expected is None:
        # Correct behavior is to abstain (erased / forbidden / genuinely unknown).
        if abstained:
            outcome = CORRECT
            return StepResult(turn.failure_class, outcome, expected, got, False, False)
        # Served something it should not have.
        return StepResult(
            turn.failure_class,
            SILENT_ERROR,
            expected,
            got,
            compliance_violation=turn.failure_class in compliance_classes,
            poisoning_success=turn.failure_class in ("poisoning", "trigger", "injection"),
        )

    # Expected a concrete value.
    if abstained:
        # A miss, but recoverable: the agent can ask / escalate rather than act
        # on a wrong fact. No downstream corruption.
        return StepResult(turn.failure_class, RECOVERABLE, expected, got, False, False)
    if got == expected:
        return StepResult(turn.failure_class, CORRECT, expected, got, False, False)
    # Confident wrong answer: the compounding, catastrophic failure.
    return StepResult(
        turn.failure_class,
        SILENT_ERROR,
        expected,
        got,
        compliance_violation=turn.failure_class in compliance_classes,
        poisoning_success=turn.failure_class in ("poisoning", "trigger", "injection"),
    )


@dataclass
class Scenario:
    scenario_id: str
    ingest: List[IngestTurn]
    queries: List[QueryTurn]
    family: str


def run_trajectory(memory, scenario: Scenario, agent: Optional[Agent] = None) -> TrajectoryResult:
    """Ingest the scenario, then run each query step through the agent policy.

    ``agent`` defaults to :class:`DeterministicAgent` (the memory-isolation path).
    Pass a :class:`NoisyAgent` to compose intrinsic agent error with memory error
    for an end-to-end task-success measurement.
    """
    if agent is None:
        agent = DeterministicAgent()
    for turn in scenario.ingest:
        memory.ingest(turn)
    steps: List[StepResult] = []
    ops = 0
    for query in scenario.queries:
        result = memory.recall(query)
        ops += result.ops
        action = agent.act(query, result)
        steps.append(classify_action(query, action))
    return TrajectoryResult(steps=steps, ops=ops)
