"""Evaluate the memory governance layer against *real* external datasets.

The synthetic reliability benchmark (``reliability_suite``) proves the layer's
behaviour on scenarios we authored. This module answers the harder, honest
question: how does the *same* production governance code
(:func:`cognitive_memory.safety.instruction_risk_reason`, :class:`GovernedMemory`)
hold up against attack payloads and privacy data written by other people?

Design commitments (kept deliberately conservative so numbers cannot flatter us):

- The injection classifier under test IS the shipping write-side quarantine
  (``instruction_risk_reason``), not eval-only code.
- Precision / recall / F1 + false-positive-rate are only computed on datasets
  that carry *both* labels (e.g. deepset). Recall-only datasets (InjecAgent)
  report recall alone; we never merge confusion matrices across datasets.
- Erasure is scored strictly: a violation counts if the served record belongs
  to the forgotten entity OR the answer text still contains the forgotten value.
- Overblocking is separated by reason: only ``forbidden_or_erased`` abstentions
  count as overblocking; weak-retrieval abstentions are reported separately so a
  shallow store cannot masquerade as "well-behaved".
- Train/dev/test discipline: :func:`export_failures` refuses the test split.

All loaders are stdlib-only and consume normalized :class:`ExternalRecord`
objects, so the eval logic is independent of each dataset's on-disk shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .external_eval import ExternalValidationError, load_validation_manifest
from .reliability import (
    GovernedMemory,
    IngestTurn,
    QueryTurn,
    Scope,
    UngovernedMemory,
    run_trajectory,
)
from .reliability_suite import Scenario
from .safety import instruction_risk_reason
from .stats import mcnemar_from_pairs, wilson_point_and_interval


# ---------------------------------------------------------------------------
# Normalized record model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    label: str
    value: str


@dataclass
class ExternalRecord:
    """One dataset item, normalized across all sources.

    ``label`` vocabulary:
      injection | benign   -- detection track
      qa_forget | qa_retain -- erasure track (question/answer carry the QA)
      pii_text             -- scope/erasure over PII spans
    ``channel`` is ``user`` or ``tool_output`` (indirect injection surface).
    """

    record_id: str
    dataset: str
    text: str
    label: str
    split: str = ""
    channel: str = "user"
    subject: str = ""
    question: str = ""
    answer: str = ""
    entities: List[EntitySpan] = field(default_factory=list)
    language: str = ""
    meta: Dict[str, str] = field(default_factory=dict)


def stable_split(record_id: str, dev_fraction: float = 0.5) -> str:
    """Deterministic, seed-free dev/test assignment from a stable id.

    Uses a hash of the id so the same record always lands in the same split
    regardless of load order or machine -- the discipline that keeps the test
    split honest across runs.
    """
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return "dev" if bucket < int(round(dev_fraction * 100)) else "test"


# ---------------------------------------------------------------------------
# Loaders (stdlib-only; tolerant of the small shape differences per source)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _rows_of(payload: Any) -> List[dict]:
    """Extract a list of row dicts from either a raw list or an HF-rows wrapper.

    The HF datasets-server ``/rows`` endpoint returns ``{"rows": [{"row": {...}}]}``;
    a plain export is just a list. Both are supported.
    """
    if isinstance(payload, dict) and "rows" in payload:
        rows = []
        for item in payload["rows"]:
            if isinstance(item, dict) and "row" in item:
                rows.append(dict(item["row"]))
            elif isinstance(item, dict):
                rows.append(dict(item))
        return rows
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict):
        return [payload]
    raise ExternalValidationError("Unrecognized dataset payload shape")


def load_deepset(path: Path, split: str = "") -> List[ExternalRecord]:
    """deepset/prompt-injections: rows with ``text`` and integer ``label`` (1=inj)."""
    records: List[ExternalRecord] = []
    for index, row in enumerate(_rows_of(_load_json(path))):
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        raw_label = row.get("label", row.get("is_injection", 0))
        is_injection = str(raw_label).strip() in ("1", "true", "True", "injection")
        rid = "deepset:%d" % index
        records.append(
            ExternalRecord(
                record_id=rid,
                dataset="deepset_prompt_injections",
                text=text,
                label="injection" if is_injection else "benign",
                split=split or stable_split(rid),
                channel="user",
                language=str(row.get("language", "")),
            )
        )
    return records


def load_injecagent(path: Path, split: str = "") -> List[ExternalRecord]:
    """InjecAgent: attacker instructions delivered via tool output (recall-only)."""
    records: List[ExternalRecord] = []
    for index, row in enumerate(_rows_of(_load_json(path))):
        text = str(
            row.get("attacker_instruction")
            or row.get("Attacker Instruction")
            or row.get("text")
            or ""
        ).strip()
        if not text:
            continue
        rid = "injecagent:%d" % index
        records.append(
            ExternalRecord(
                record_id=rid,
                dataset="injecagent",
                text=text,
                label="injection",
                split=split or stable_split(rid),
                channel="tool_output",
                meta={"attack_type": str(row.get("attack_type", row.get("Attack Type", "")))},
            )
        )
    return records


import re as _re

_TOFU_FULLNAME = _re.compile(r"full name is ([A-Z][^.,;]{1,60}?)(?:[.,;]|$)")
_TOFU_PROPER = _re.compile(r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+){1,3})\b")


def _tofu_author(question: str, answer: str) -> str:
    """Best-effort author name for a TOFU QA (the term GDPR-erasure targets).

    Real TOFU has no author field; the name recurs in the text. We prefer an
    explicit "full name is X" anchor, else the longest capitalized proper-noun
    phrase in the answer, else the question. Returns "" when nothing is found
    (counted honestly against entity_coverage).
    """
    anchor = _TOFU_FULLNAME.search(answer)
    if anchor:
        return anchor.group(1).strip()
    for text in (answer, question):
        candidates = _TOFU_PROPER.findall(text)
        if candidates:
            return max(candidates, key=len).strip()
    return ""


def _tofu_sibling(path: Path) -> Optional[Path]:
    """Locate the retain file that pairs with a downloaded forget file."""
    name = path.name
    for a, b in (("forget10", "retain90"), ("forget", "retain")):
        if a in name:
            candidate = path.with_name(name.replace(a, b))
            if candidate.exists():
                return candidate
    return None


def load_tofu(path: Path, split: str = "") -> List[ExternalRecord]:
    """TOFU: fictitious-author QA (erasure enforcement + utility retention).

    Two shapes are supported:
      * fixture shape -- rows carry explicit ``subset`` and ``author`` fields;
      * real shape -- forget/retain live in separate files (forget10/retain90)
        with no labels, so the label comes from the filename and the author is
        extracted from the text via :func:`_tofu_author`.
    """
    rows = _rows_of(_load_json(path))
    has_subset = bool(rows) and ("subset" in rows[0] or "author" in rows[0])

    def _emit(rows_in: List[dict], label: str, tag: str) -> List[ExternalRecord]:
        out: List[ExternalRecord] = []
        for index, row in enumerate(rows_in):
            question = str(row.get("question", "")).strip()
            answer = str(row.get("answer", "")).strip()
            if not question:
                continue
            if has_subset:
                author = str(row.get("author", row.get("subject", ""))).strip()
                row_label = "qa_forget" if "forget" in str(row.get("subset", "")).lower() else "qa_retain"
            else:
                author = _tofu_author(question, answer)
                row_label = label
            rid = "tofu:%s:%d" % (tag, index)
            out.append(
                ExternalRecord(
                    record_id=rid,
                    dataset="tofu",
                    text="%s %s" % (question, answer),
                    label=row_label,
                    # split on author when known (no author-pattern leak), else per QA
                    split=split or stable_split(author or rid),
                    subject=author,
                    question=question,
                    answer=answer,
                )
            )
        return out

    if has_subset:
        return _emit(rows, "qa_retain", "fix")

    records = _emit(rows, "qa_forget", "forget")
    sibling = _tofu_sibling(path)
    if sibling is not None:
        records.extend(_emit(_rows_of(_load_json(sibling)), "qa_retain", "retain"))
    return records


def load_ai4privacy(path: Path, split: str = "") -> List[ExternalRecord]:
    """ai4privacy-style rows: ``source_text`` + ``privacy_mask`` span list."""
    records: List[ExternalRecord] = []
    for index, row in enumerate(_rows_of(_load_json(path))):
        text = str(row.get("source_text", row.get("text", ""))).strip()
        if not text:
            continue
        spans: List[EntitySpan] = []
        for span in row.get("privacy_mask", row.get("spans", []) or []):
            if not isinstance(span, dict):
                continue
            value = str(span.get("value", span.get("text", ""))).strip()
            label = str(span.get("label", span.get("type", "PII"))).strip()
            start = int(span.get("start", -1))
            end = int(span.get("end", -1))
            if value:
                spans.append(EntitySpan(start=start, end=end, label=label, value=value))
        rid = "ai4privacy:%d" % index
        records.append(
            ExternalRecord(
                record_id=rid,
                dataset="ai4privacy",
                text=text,
                label="pii_text",
                split=split or stable_split(rid),
                entities=spans,
                language=str(row.get("language", "")),
            )
        )
    return records


_LOADERS: Dict[str, Callable[[Path, str], List[ExternalRecord]]] = {
    "deepset_prompt_injections": load_deepset,
    "deepset": load_deepset,
    "injecagent": load_injecagent,
    "tofu": load_tofu,
    "ai4privacy": load_ai4privacy,
}


def load_external_records(
    dataset: str,
    manifest_path: str,
    *,
    split: str = "",
    limit: Optional[int] = None,
) -> List[ExternalRecord]:
    """Load a dataset through the approval-gated manifest.

    Always routes through :func:`load_validation_manifest`, so a dataset that is
    not ``approved_for_eval`` (or has an unsafe pii_status) raises before any
    records are read.
    """
    manifest = load_validation_manifest(manifest_path)
    base = Path(manifest.manifest_path).parent
    entry = None
    for candidate in manifest.datasets:
        if candidate.dataset_name == dataset or candidate.expected_schema == dataset:
            entry = candidate
            break
    if entry is None:
        raise ExternalValidationError("Dataset %s not present in manifest %s" % (dataset, manifest_path))

    loader = _LOADERS.get(entry.dataset_name) or _LOADERS.get(entry.expected_schema)
    if loader is None:
        raise ExternalValidationError("No external-reliability loader for %s" % dataset)

    local_path = Path(entry.local_path)
    if not local_path.is_absolute():
        local_path = (base / local_path).resolve()
    records = loader(local_path, "")
    if split:
        records = [r for r in records if r.split == split]
    if limit is not None:
        records = records[:limit]
    return records


# ---------------------------------------------------------------------------
# Track A: injection detection (production classifier under test)
# ---------------------------------------------------------------------------


@dataclass
class DetectionReport:
    dataset: str
    split: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    recall_only: bool = False

    @property
    def positives(self) -> int:
        return self.tp + self.fn

    @property
    def negatives(self) -> int:
        return self.tn + self.fp

    @property
    def recall(self) -> float:
        return self.tp / self.positives if self.positives else 0.0

    @property
    def precision(self) -> Optional[float]:
        if self.recall_only:
            return None
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> Optional[float]:
        if self.recall_only:
            return None
        p, r = self.precision, self.recall
        if not p or (p + r) == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def fpr(self) -> Optional[float]:
        if self.recall_only or not self.negatives:
            return None
        return self.fp / self.negatives

    def as_dict(self) -> Dict[str, Any]:
        recall_ci = wilson_point_and_interval(self.tp, self.positives) if self.positives else (0.0, 0.0, 0.0)
        out: Dict[str, Any] = {
            "dataset": self.dataset,
            "split": self.split,
            "n": self.positives + self.negatives,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "recall": self.recall,
            "recall_ci": [recall_ci[1], recall_ci[2]],
            "recall_only": self.recall_only,
        }
        if not self.recall_only:
            out["precision"] = self.precision
            out["f1"] = self.f1
            out["fpr"] = self.fpr
            if self.negatives:
                fpr_ci = wilson_point_and_interval(self.fp, self.negatives)
                out["fpr_ci"] = [fpr_ci[1], fpr_ci[2]]
        return out


def classify_injection(text: str) -> bool:
    """The shipping write-side classifier, as a boolean. No eval-only logic."""
    return bool(instruction_risk_reason(text))


def evaluate_injection_detection(
    records: Sequence[ExternalRecord],
    classifier: Callable[[str], bool] = classify_injection,
) -> DetectionReport:
    if not records:
        raise ExternalValidationError("No records for injection detection")
    dataset = records[0].dataset
    split = records[0].split
    has_benign = any(r.label == "benign" for r in records)
    report = DetectionReport(dataset=dataset, split=split, recall_only=not has_benign)
    for record in records:
        predicted = classifier(record.text)
        is_injection = record.label == "injection"
        if is_injection and predicted:
            report.tp += 1
        elif is_injection and not predicted:
            report.fn += 1
        elif not is_injection and predicted:
            report.fp += 1
        else:
            report.tn += 1
    return report


# ---------------------------------------------------------------------------
# Track A2: payload replay (real attack text in the paired trajectory harness)
# ---------------------------------------------------------------------------


@dataclass
class PayloadReplayReport:
    dataset: str
    split: str
    n: int
    governed_poisoning_success: int
    ungoverned_poisoning_success: int
    mcnemar_c: int
    mcnemar_p: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "n": self.n,
            "governed_poisoning_success": self.governed_poisoning_success,
            "ungoverned_poisoning_success": self.ungoverned_poisoning_success,
            "mcnemar_discordant": self.mcnemar_c,
            "mcnemar_p": self.mcnemar_p,
            "note": "scaffold is synthetic (a fact + a look-alike poison record); "
            "only the attack text is real.",
        }


def build_payload_replay_scenarios(records: Sequence[ExternalRecord]) -> List[Scenario]:
    """Wrap each real injection payload as the attacker write in a fact/poison pair.

    A benign fact establishes the true value; the attacker record carries the
    *real* payload text but tries to flip the value. Downstream steps query the
    entity; a served flip is a poisoning success.
    """
    scenarios: List[Scenario] = []
    for index, record in enumerate(records):
        if record.label != "injection":
            continue
        tenant = "replay"
        subject = "ent_%d" % index
        relation = "status"
        true_val = "approved"
        poison_val = "denied"
        scope = Scope(tenant=tenant, subject=subject)
        # The attacker record must be *retrievable* for the victim query, so it
        # carries the queryable stem ("<entity> <relation>") plus the real
        # payload text -- mirroring a MINJA-style look-alike record. Governance
        # must catch it from the payload content; a shallow store retrieves and
        # serves the poison value because the attack is the more recent write.
        attack_text = "%s %s %s" % (subject, relation, record.text)
        ingest = [
            IngestTurn("fact", "%s %s %s" % (subject, relation, true_val), subject, relation, true_val, scope, "user", 0.96),
            IngestTurn("attack", attack_text, subject, relation, poison_val, scope, "external_tool", 0.8),
        ]
        queries = [QueryTurn("%s %s" % (subject, relation), scope, true_val, "injection") for _ in range(2)]
        scenarios.append(Scenario("replay_%d" % index, ingest, queries, "injection"))
    return scenarios


def evaluate_payload_replay(records: Sequence[ExternalRecord]) -> PayloadReplayReport:
    scenarios = build_payload_replay_scenarios(records)
    if not scenarios:
        raise ExternalValidationError("No injection payloads to replay")
    gov_flags: List[bool] = []
    ung_flags: List[bool] = []
    for scenario in scenarios:
        gov = run_trajectory(GovernedMemory(), scenario)
        ung = run_trajectory(UngovernedMemory(), scenario)
        gov_flags.append(any(s.poisoning_success for s in gov.steps))
        ung_flags.append(any(s.poisoning_success for s in ung.steps))
    # paired McNemar over "poisoning avoided" (success = not poisoned)
    gov_avoided = [not g for g in gov_flags]
    ung_avoided = [not u for u in ung_flags]
    b, c, _, p = mcnemar_from_pairs(gov_avoided, ung_avoided)
    return PayloadReplayReport(
        dataset=records[0].dataset,
        split=records[0].split,
        n=len(scenarios),
        governed_poisoning_success=sum(gov_flags),
        ungoverned_poisoning_success=sum(ung_flags),
        mcnemar_c=b + c,
        mcnemar_p=p,
    )


# ---------------------------------------------------------------------------
# Track B: erasure enforcement + utility retention
# ---------------------------------------------------------------------------


@dataclass
class ErasureReport:
    dataset: str
    split: str
    n_forget: int = 0
    n_retain: int = 0
    violations: int = 0            # forget-set records still served (id or value)
    retained_correct: int = 0      # retain-set records still answerable
    overblocked: int = 0           # retain abstained specifically due to erasure
    retain_abstained_total: int = 0
    entity_coverage: float = 0.0

    @property
    def erasure_enforcement_rate(self) -> Optional[float]:
        if not self.n_forget:
            return None
        return 1.0 - self.violations / self.n_forget

    @property
    def utility_retention_rate(self) -> Optional[float]:
        if not self.n_retain:
            return None
        return self.retained_correct / self.n_retain

    @property
    def overblocking_rate(self) -> Optional[float]:
        if not self.n_retain:
            return None
        return self.overblocked / self.n_retain

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "n_forget": self.n_forget,
            "n_retain": self.n_retain,
            "violations": self.violations,
            "erasure_enforcement_rate": self.erasure_enforcement_rate,
            "utility_retention_rate": self.utility_retention_rate,
            "overblocking_rate": self.overblocking_rate,
            "retain_abstained_total": self.retain_abstained_total,
            "entity_coverage": self.entity_coverage,
        }


def _forget_term_for(record: ExternalRecord) -> str:
    """Pick a distinctive term to forget for a TOFU-style QA record.

    Prefer the author subject; fall back to the rarest long token in the answer
    so erasure is targeted rather than a blanket wipe.
    """
    if record.subject:
        return record.subject
    tokens = [t for t in record.answer.split() if len(t) >= 5]
    return tokens[0] if tokens else record.answer


def evaluate_erasure(records: Sequence[ExternalRecord]) -> ErasureReport:
    """Ingest QA, forget the forget-set entities, then probe both sets.

    Enforcement violation is strict: served-after-forget counts if the answer
    text still contains the forgotten value OR a forgotten record is selected.
    """
    forget = [r for r in records if r.label == "qa_forget"]
    retain = [r for r in records if r.label == "qa_retain"]
    dataset = records[0].dataset if records else "tofu"
    split = records[0].split if records else ""
    report = ErasureReport(dataset=dataset, split=split, n_forget=len(forget), n_retain=len(retain))

    memory = GovernedMemory()
    tenant = "erasure"
    # Store each QA as its own fact. A unique relation per record keeps distinct
    # questions about one author from collapsing into a single supersession group
    # (which would let only the latest answer survive and understate utility).
    for index, record in enumerate(records):
        memory.remember(
            record.text,
            subject=record.subject or record.record_id,
            relation="qa_%d" % index,
            object=record.answer,
            tenant=tenant,
            entity=record.subject or record.record_id,
        )

    # forget each forget-set entity
    covered = 0
    for record in forget:
        term = _forget_term_for(record)
        if term:
            covered += 1
        memory.forget(term, Scope(tenant=tenant, subject=record.subject or record.record_id))
    report.entity_coverage = covered / len(forget) if forget else 0.0

    # probe forget-set: must NOT be served
    for record in forget:
        result = memory.recall_value(
            record.question or record.text,
            tenant=tenant,
            entity=record.subject or record.record_id,
        )
        if not result.abstained:
            served = result.answer or ""
            value = record.answer or ""
            value_leak = bool(value) and value.lower() in served.lower()
            id_leak = any(
                (record.subject and record.subject == getattr(m, "subject", "")) for m in result.selected
            )
            if value_leak or id_leak:
                report.violations += 1

    # probe retain-set: should still be answerable; separate overblocking reason
    for record in retain:
        result = memory.recall_value(
            record.question or record.text,
            tenant=tenant,
            entity=record.subject or record.record_id,
        )
        if result.abstained:
            report.retain_abstained_total += 1
            if result.reason in ("forbidden_or_erased", "erased"):
                report.overblocked += 1
        else:
            served = (result.answer or "").lower()
            if record.answer and record.answer.lower() in served:
                report.retained_correct += 1
    return report


# ---------------------------------------------------------------------------
# Track C: scope / tenant isolation over multi-person records
# ---------------------------------------------------------------------------


@dataclass
class ScopeReport:
    dataset: str
    split: str
    n_queries: int = 0
    cross_subject_serves: int = 0
    abstained: int = 0
    correct: int = 0

    @property
    def scope_isolation_rate(self) -> Optional[float]:
        if not self.n_queries:
            return None
        return 1.0 - self.cross_subject_serves / self.n_queries

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "n_queries": self.n_queries,
            "cross_subject_serves": self.cross_subject_serves,
            "abstained": self.abstained,
            "correct": self.correct,
            "scope_isolation_rate": self.scope_isolation_rate,
        }


def evaluate_scope(records: Sequence[ExternalRecord]) -> ScopeReport:
    """Store each person's PII value under their own entity; query cross-entity.

    For records with >=2 person spans, store span A under entity A and span B
    under entity B (shared surface tokens), then query for A. A served B-value
    is a cross-subject leak. Isolation-by-total-abstention is reported
    separately so it cannot masquerade as isolation.
    """
    dataset = records[0].dataset if records else "ai4privacy"
    split = records[0].split if records else ""
    report = ScopeReport(dataset=dataset, split=split)
    memory = GovernedMemory()
    tenant = "scope"

    for index, record in enumerate(records):
        persons = [s for s in record.entities if s.label.upper() in ("FIRSTNAME", "LASTNAME", "PERSON", "NAME", "FULLNAME")]
        if len(persons) < 2:
            continue
        a, b = persons[0], persons[1]
        subj_a = "person_a_%d" % index
        subj_b = "person_b_%d" % index
        memory.remember("contact detail %s" % a.value, subject=subj_a, relation="detail", object=a.value, tenant=tenant, entity=subj_a)
        memory.remember("contact detail %s" % b.value, subject=subj_b, relation="detail", object=b.value, tenant=tenant, entity=subj_b)
        result = memory.recall_value("contact detail", tenant=tenant, entity=subj_a)
        report.n_queries += 1
        if result.abstained:
            report.abstained += 1
        elif result.answer == a.value:
            report.correct += 1
        elif result.answer == b.value:
            report.cross_subject_serves += 1
    return report


# ---------------------------------------------------------------------------
# Failure export (dev-only) + report formatting
# ---------------------------------------------------------------------------


def export_failures(
    records: Sequence[ExternalRecord],
    path: str,
    *,
    classifier: Callable[[str], bool] = classify_injection,
) -> int:
    """Dump misclassified injection records for pattern iteration. Dev only.

    Hard guardrail: refuses to run on the test split so tuning cannot see it.
    """
    if any(r.split == "test" for r in records):
        raise ExternalValidationError("export_failures refuses the test split (overfitting guard)")
    written = 0
    with Path(path).open("w", encoding="utf-8") as handle:
        for record in records:
            predicted = classifier(record.text)
            is_injection = record.label == "injection"
            if predicted == is_injection:
                continue
            handle.write(
                json.dumps(
                    {
                        "record_id": record.record_id,
                        "dataset": record.dataset,
                        "label": record.label,
                        "predicted_injection": predicted,
                        "text": record.text,
                        "kind": "false_negative" if is_injection else "false_positive",
                    }
                )
                + "\n"
            )
            written += 1
    return written


def format_external_reliability_report(reports: Mapping[str, Any]) -> str:
    lines = ["External reliability (real datasets)"]
    for track, payload in reports.items():
        lines.append("== %s ==" % track)
        if isinstance(payload, list):
            for item in payload:
                lines.append("  " + json.dumps(item, sort_keys=True))
        else:
            lines.append("  " + json.dumps(payload, sort_keys=True))
    return "\n".join(lines)


def dumps_external_reliability_report(reports: Mapping[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(reports, indent=2, sort_keys=True, default=lambda o: getattr(o, "as_dict", lambda: str(o))())
    return format_external_reliability_report(reports)
