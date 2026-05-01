from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .controller import MemoryController
from .extractor import NoisyRuleBasedExtractor, RecruitingRuleBasedExtractor
from .models import Episode, RetrievalRequest, ensure_datetime, tokenize
from .persistence import retrieval_trace_record, save_snapshot
from .retrieval import RetrievalPlanner


SUPPORTED_DOMAINS = {"recruiting", "customer_service", "appointment", "handwerk", "generic"}


@dataclass
class CallerIdentity:
    phone_number_hash: str = ""
    stated_name: str = ""
    crm_id: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallerIdentity":
        return cls(
            phone_number_hash=str(data.get("phone_number_hash", "")),
            stated_name=str(data.get("stated_name", "")),
            crm_id=str(data.get("crm_id", "")),
            confidence=float(data.get("confidence", 0.0)),
        )


@dataclass
class TranscriptParticipant:
    speaker_id: str
    role: str = "unknown"
    name: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptParticipant":
        return cls(
            speaker_id=str(data.get("speaker_id", "")),
            role=str(data.get("role", "unknown")),
            name=str(data.get("name", "")),
        )


@dataclass
class TranscriptTurn:
    speaker: str
    text: str
    timestamp: Optional[datetime] = None
    asr_confidence: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptTurn":
        timestamp = data.get("timestamp")
        return cls(
            speaker=str(data.get("speaker", "")),
            text=str(data.get("text", "")),
            timestamp=ensure_datetime(timestamp) if timestamp else None,
            asr_confidence=float(data["asr_confidence"]) if data.get("asr_confidence") is not None else None,
        )


@dataclass
class TranscriptLabels:
    facts_that_should_be_stored: List[str] = field(default_factory=list)
    facts_that_should_not_be_stored: List[str] = field(default_factory=list)
    sensitive_facts: List[str] = field(default_factory=list)
    consent_required_facts: List[str] = field(default_factory=list)
    do_not_use_constraints: List[Dict[str, Any]] = field(default_factory=list)
    do_not_contact_constraints: List[Dict[str, Any]] = field(default_factory=list)
    current_truth: List[Dict[str, Any]] = field(default_factory=list)
    historical_truth: List[Dict[str, Any]] = field(default_factory=list)
    expected_abstentions: List[Dict[str, Any]] = field(default_factory=list)
    expected_follow_up_actions: List[Dict[str, Any]] = field(default_factory=list)
    forbidden_outputs: List[str] = field(default_factory=list)
    identity_resolution_expected: Dict[str, Any] = field(default_factory=dict)
    scope_expected: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TranscriptLabels":
        if not data:
            return cls()
        return cls(
            facts_that_should_be_stored=_strings(data.get("facts_that_should_be_stored", [])),
            facts_that_should_not_be_stored=_strings(data.get("facts_that_should_not_be_stored", [])),
            sensitive_facts=_strings(data.get("sensitive_facts", [])),
            consent_required_facts=_strings(data.get("consent_required_facts", [])),
            do_not_use_constraints=_checks(data.get("do_not_use_constraints", [])),
            do_not_contact_constraints=_checks(data.get("do_not_contact_constraints", [])),
            current_truth=_checks(data.get("current_truth", [])),
            historical_truth=_checks(data.get("historical_truth", [])),
            expected_abstentions=_checks(data.get("expected_abstentions", [])),
            expected_follow_up_actions=_checks(data.get("expected_follow_up_actions", [])),
            forbidden_outputs=_strings(data.get("forbidden_outputs", [])),
            identity_resolution_expected=dict(data.get("identity_resolution_expected", {})),
            scope_expected=_checks(data.get("scope_expected", [])),
        )

    def has_labels(self) -> bool:
        return any(
            [
                self.facts_that_should_be_stored,
                self.facts_that_should_not_be_stored,
                self.sensitive_facts,
                self.consent_required_facts,
                self.do_not_use_constraints,
                self.do_not_contact_constraints,
                self.current_truth,
                self.historical_truth,
                self.expected_abstentions,
                self.expected_follow_up_actions,
                self.forbidden_outputs,
                self.identity_resolution_expected,
                self.scope_expected,
            ]
        )


@dataclass
class Transcript:
    transcript_id: str
    domain: str
    timestamp: datetime
    caller_identity: CallerIdentity = field(default_factory=CallerIdentity)
    participants: List[TranscriptParticipant] = field(default_factory=list)
    turns: List[TranscriptTurn] = field(default_factory=list)
    existing_context: Dict[str, Any] = field(default_factory=dict)
    expected_labels: TranscriptLabels = field(default_factory=TranscriptLabels)
    redaction_terms: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Transcript":
        domain = str(data.get("domain", "generic"))
        if domain not in SUPPORTED_DOMAINS:
            domain = "generic"
        return cls(
            transcript_id=str(data.get("transcript_id", "")),
            domain=domain,
            timestamp=ensure_datetime(data.get("timestamp")),
            caller_identity=CallerIdentity.from_dict(dict(data.get("caller_identity", {}))),
            participants=[TranscriptParticipant.from_dict(item) for item in data.get("participants", [])],
            turns=[TranscriptTurn.from_dict(item) for item in data.get("turns", [])],
            existing_context=dict(data.get("existing_context", {})),
            expected_labels=TranscriptLabels.from_dict(data.get("expected_labels")),
            redaction_terms=_strings(data.get("redaction_terms", [])),
        )


@dataclass
class TranscriptEvaluationResult:
    transcript_id: str
    domain: str
    labeled: bool
    metrics: Dict[str, float]
    diagnostics: Dict[str, Any]
    failures: List[str]
    latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TranscriptRedactor:
    """Basic report redaction helper, not production anonymization."""

    def __init__(
        self,
        terms: Optional[Iterable[str]] = None,
        redact_salaries: bool = False,
        redact_companies: bool = False,
    ) -> None:
        self.terms = [term for term in (terms or []) if term]
        self.redact_salaries = redact_salaries
        self.redact_companies = redact_companies

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        return value

    def _redact_text(self, text: str) -> str:
        redacted = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", text)
        redacted = re.sub(r"\+?\d[\d ()-]{6,}\d", "[PHONE]", redacted)
        if self.redact_salaries:
            redacted = re.sub(r"\b\d{2,3}k\b", "[SALARY]", redacted, flags=re.IGNORECASE)
            redacted = re.sub(r"\b\d{5,6}\s*(?:eur|euro|usd|dollars?)?\b", "[SALARY]", redacted, flags=re.IGNORECASE)
        if self.redact_companies:
            redacted = re.sub(r"\b(?:client|company|employer)_?[A-Z][A-Za-z0-9_-]*\b", "[COMPANY]", redacted)
        for term in sorted(self.terms, key=len, reverse=True):
            redacted = re.sub(re.escape(term), "[REDACTED]", redacted, flags=re.IGNORECASE)
        return redacted


def load_transcripts(path: str) -> List[Transcript]:
    source = Path(path)
    files: List[Path]
    if source.is_dir():
        files = sorted(item for item in source.rglob("*") if item.suffix.lower() in (".json", ".jsonl"))
    else:
        files = [source]

    transcripts: List[Transcript] = []
    for file_path in files:
        if file_path.suffix.lower() == ".jsonl":
            with file_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        transcripts.append(Transcript.from_dict(json.loads(stripped)))
        elif file_path.suffix.lower() == ".json":
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, list):
                transcripts.extend(Transcript.from_dict(item) for item in payload)
            else:
                transcripts.append(Transcript.from_dict(payload))
    return transcripts


def transcript_to_episodes(transcript: Transcript) -> List[Episode]:
    episodes = _context_episodes(transcript)
    participant_roles = {participant.speaker_id: participant.role for participant in transcript.participants}
    ambiguous_identity = transcript.caller_identity.confidence < 0.5
    for index, turn in enumerate(transcript.turns):
        role = participant_roles.get(turn.speaker, "unknown")
        timestamp = turn.timestamp or transcript.timestamp
        source = _source_for_role(role, transcript.domain)
        sensitivity = "high" if _looks_sensitive(turn.text) else "low"
        consent_basis = "none" if sensitivity == "high" else "implicit"
        content = turn.text
        if ambiguous_identity and role not in ("agent", "recruiter"):
            content = "IDENTITY_AMBIGUOUS content withheld pending identity resolution"
        episode = Episode(
            content=content,
            actor=role,
            source=source,
            user_id=transcript.caller_identity.crm_id or transcript.caller_identity.phone_number_hash or "unknown_caller",
            project_id=transcript.domain,
            context_id=transcript.transcript_id,
            sensitivity=sensitivity,
            consent_basis=consent_basis,
            timestamp=timestamp,
        )
        episode.id = "%s_turn_%03d" % (transcript.transcript_id, index + 1)
        episodes.append(episode)
    return episodes


def evaluate_transcripts(
    input_path: str,
    persist_path: str = "",
) -> Dict[str, Any]:
    transcripts = load_transcripts(input_path)
    results = [evaluate_transcript(transcript, persist_path=persist_path if len(transcripts) == 1 else "") for transcript in transcripts]
    summary = _aggregate_results(results)
    return {
        "transcript_count": len(transcripts),
        "labeled_count": sum(1 for item in results if item.labeled),
        "summary": summary,
        "results": [item.to_dict() for item in results],
        "redaction_terms": _report_redaction_terms(transcripts),
    }


def evaluate_transcript(transcript: Transcript, persist_path: str = "") -> TranscriptEvaluationResult:
    started = time.perf_counter()
    controller = MemoryController(extractor=_extractor_for_domain(transcript.domain))
    retrieval = RetrievalPlanner(controller.store, controller.policy)
    episodes = transcript_to_episodes(transcript)
    for episode in episodes:
        controller.ingest_episode(episode)

    labels = transcript.expected_labels
    candidate_claims = [candidate.claim for candidate in controller.store.candidates.values()]
    fact_claims = [fact.claim_text for fact in controller.store.list_facts()]
    failures: List[str] = []
    retrieval_records: List[Dict[str, Any]] = []

    metrics: Dict[str, float] = {}
    if labels.has_labels():
        metrics.update(_candidate_metrics(labels, candidate_claims))
        metrics.update(_fact_metrics(labels, fact_claims))
        metrics["identity_resolution_accuracy"] = _identity_accuracy(transcript)
        current = _evaluate_checks(labels.current_truth, retrieval, transcript, "temporal")
        historical = _evaluate_checks(labels.historical_truth, retrieval, transcript, "temporal")
        abstentions = _evaluate_checks(labels.expected_abstentions, retrieval, transcript, "compliance")
        scope = _evaluate_checks(labels.scope_expected, retrieval, transcript, "personalized")
        do_not_use = _evaluate_checks(labels.do_not_use_constraints, retrieval, transcript, "compliance")
        do_not_contact = _evaluate_checks(labels.do_not_contact_constraints, retrieval, transcript, "compliance")
        follow_up = _evaluate_checks(labels.expected_follow_up_actions, retrieval, transcript, "planning")
        retrieval_records.extend(
            current.records
            + historical.records
            + abstentions.records
            + scope.records
            + do_not_use.records
            + do_not_contact.records
            + follow_up.records
        )
        metrics["current_truth_accuracy"] = current.accuracy
        metrics["historical_truth_accuracy"] = historical.accuracy
        metrics["abstention_accuracy"] = abstentions.accuracy
        metrics["scope_accuracy"] = scope.accuracy
        metrics["do_not_use_leakage"] = do_not_use.leakage_rate
        metrics["follow_up_action_safety"] = follow_up.accuracy
        metrics["provenance_coverage"] = _provenance_coverage(retrieval_records)
        if do_not_contact.total:
            metrics["do_not_contact_leakage"] = do_not_contact.leakage_rate
        failures.extend(_failures_from_metrics(metrics))
    else:
        metrics = {"diagnostic_only": 1.0}
        failures.append("unlabeled_diagnostic_only")

    metrics["transcript_to_memory_latency"] = (time.perf_counter() - started) * 1000.0
    if persist_path:
        save_snapshot(persist_path, controller.store, controller.policy, retrieval_traces=[item["result"] for item in retrieval_records])

    diagnostics = _diagnostics(controller, retrieval_records)
    diagnostics["episodes_created"] = len(episodes)
    return TranscriptEvaluationResult(
        transcript_id=transcript.transcript_id,
        domain=transcript.domain,
        labeled=labels.has_labels(),
        metrics=metrics,
        diagnostics=diagnostics,
        failures=failures,
        latency_ms=metrics["transcript_to_memory_latency"],
    )


def dumps_transcript_report(
    report: Dict[str, Any],
    as_json: bool = False,
    redaction_terms: Optional[Iterable[str]] = None,
    redact_salaries: bool = False,
    redact_companies: bool = False,
) -> str:
    terms = list(redaction_terms or []) + _strings(report.get("redaction_terms", []))
    report_without_terms = dict(report)
    report_without_terms.pop("redaction_terms", None)
    redactor = TranscriptRedactor(
        terms=terms,
        redact_salaries=redact_salaries,
        redact_companies=redact_companies,
    )
    safe_report = redactor.redact(report_without_terms)
    if as_json:
        return json.dumps(safe_report, indent=2, sort_keys=True)

    lines = [
        "Transcript evaluation",
        "transcripts: %s labeled: %s" % (safe_report["transcript_count"], safe_report["labeled_count"]),
    ]
    summary = safe_report.get("summary", {})
    for key in sorted(summary):
        value = summary[key]
        if isinstance(value, float):
            lines.append("%s: %.4f" % (key, value))
    for result in safe_report.get("results", []):
        failures = ", ".join(result.get("failures", [])) or "none"
        lines.append(
            "%s [%s] labeled=%s failures=%s"
            % (result.get("transcript_id"), result.get("domain"), result.get("labeled"), failures)
        )
    return "\n".join(lines)


def _extractor_for_domain(domain: str) -> NoisyRuleBasedExtractor:
    if domain == "recruiting":
        return RecruitingRuleBasedExtractor()
    return NoisyRuleBasedExtractor()


def _report_redaction_terms(transcripts: Sequence[Transcript]) -> List[str]:
    terms = []
    for transcript in transcripts:
        terms.extend(transcript.redaction_terms)
        if transcript.caller_identity.stated_name:
            terms.append(transcript.caller_identity.stated_name)
        terms.extend(participant.name for participant in transcript.participants if participant.name)
        labels = transcript.expected_labels
        terms.extend(labels.sensitive_facts)
        terms.extend(labels.consent_required_facts)
        terms.extend(labels.forbidden_outputs)
    return sorted({term for term in terms if term})


def _context_episodes(transcript: Transcript) -> List[Episode]:
    episodes: List[Episode] = []
    context = transcript.existing_context or {}
    entries: List[Tuple[str, str, Optional[Any]]] = []
    for key, source in (("crm_facts", "crm"), ("prior_tickets", "tool"), ("previous_call_summaries", "chat")):
        for item in context.get(key, []):
            if isinstance(item, str):
                entries.append((item, source, None))
            elif isinstance(item, dict):
                entries.append((str(item.get("content", "")), str(item.get("source", source)), item.get("timestamp")))
    for index, (content, source, timestamp_value) in enumerate(entries):
        if not content:
            continue
        episode = Episode(
            content=content,
            actor="system",
            source=source,
            user_id=transcript.caller_identity.crm_id or transcript.caller_identity.phone_number_hash or "unknown_caller",
            project_id=transcript.domain,
            context_id=transcript.transcript_id,
            timestamp=ensure_datetime(timestamp_value) if timestamp_value else transcript.timestamp,
        )
        episode.id = "%s_context_%03d" % (transcript.transcript_id, index + 1)
        episodes.append(episode)
    return episodes


def _source_for_role(role: str, domain: str) -> str:
    if role == "candidate":
        return "candidate"
    if role == "client":
        return "client"
    if role in ("agent", "recruiter"):
        return "recruiter_note"
    if domain in ("customer_service", "appointment", "handwerk"):
        return "chat"
    return "chat"


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("migraine", "medical", "diagnosis", "ssn", "home address", "child", "kid"))


@dataclass
class CheckEvaluation:
    total: int
    passed: int
    records: List[Dict[str, Any]]
    leakage_count: int = 0

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 1.0
        return self.passed / float(self.total)

    @property
    def leakage_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.leakage_count / float(self.total)


def _evaluate_checks(
    checks: Sequence[Dict[str, Any]],
    retrieval: RetrievalPlanner,
    transcript: Transcript,
    default_task_type: str,
) -> CheckEvaluation:
    records: List[Dict[str, Any]] = []
    passed = 0
    leakage_count = 0
    for check in checks:
        query = str(check.get("query", ""))
        if not query:
            continue
        request = RetrievalRequest(
            query=query,
            user_id=transcript.caller_identity.crm_id or transcript.caller_identity.phone_number_hash or "unknown_caller",
            project_id=transcript.domain,
            task_type=str(check.get("task_type", default_task_type)),
            time_scope=str(check.get("time_scope", "current")),
            as_of=check.get("as_of"),
            top_k=int(check.get("top_k", 5)),
        )
        result = retrieval.retrieve(request)
        answer = result.answer_text()
        include_ok = _contains_all(answer, _strings(check.get("include", [])))
        exclude_ok = not _contains_any(answer, _strings(check.get("exclude", [])))
        expected_abstain = bool(check.get("expected_abstain", False))
        abstain_ok = result.abstain_recommended if expected_abstain else not result.abstain_recommended
        forbidden = _strings(check.get("forbidden_outputs", []))
        forbidden_ok = not _contains_any(answer, forbidden)
        check_passed = include_ok and exclude_ok and abstain_ok and forbidden_ok
        if check_passed:
            passed += 1
        if not forbidden_ok or not exclude_ok:
            leakage_count += 1
        records.append(
            {
                "query": query,
                "answer": answer,
                "passed": check_passed,
                "result": retrieval_trace_record(result, query=query),
                "abstain_reason": result.abstain_reason,
            }
        )
    return CheckEvaluation(total=len([check for check in checks if check.get("query")]), passed=passed, records=records, leakage_count=leakage_count)


def _candidate_metrics(labels: TranscriptLabels, candidate_claims: Sequence[str]) -> Dict[str, float]:
    expected = labels.facts_that_should_be_stored
    negatives = labels.facts_that_should_not_be_stored + labels.sensitive_facts + labels.consent_required_facts
    return {
        "extraction_recall": _label_recall(expected, candidate_claims),
        "extraction_precision": _negative_precision(candidate_claims, negatives),
    }


def _fact_metrics(labels: TranscriptLabels, fact_claims: Sequence[str]) -> Dict[str, float]:
    expected = labels.facts_that_should_be_stored
    negatives = labels.facts_that_should_not_be_stored + labels.sensitive_facts + labels.consent_required_facts
    sensitive_hits = _label_hits(labels.sensitive_facts + labels.consent_required_facts, fact_claims)
    sensitive_total = len(labels.sensitive_facts + labels.consent_required_facts)
    return {
        "memory_write_recall": _label_recall(expected, fact_claims),
        "memory_write_precision": _negative_precision(fact_claims, negatives),
        "sensitive_storage_violation_rate": sensitive_hits / float(sensitive_total) if sensitive_total else 0.0,
    }


def _label_recall(labels: Sequence[str], values: Sequence[str]) -> float:
    if not labels:
        return 1.0
    return _label_hits(labels, values) / float(len(labels))


def _negative_precision(values: Sequence[str], negative_labels: Sequence[str]) -> float:
    if not values:
        return 1.0
    bad = _label_hits(negative_labels, values)
    return max(0.0, 1.0 - bad / float(len(values)))


def _label_hits(labels: Sequence[str], values: Sequence[str]) -> int:
    return sum(1 for label in labels if _contains_any(" | ".join(values), [label]))


def _identity_accuracy(transcript: Transcript) -> float:
    expected = transcript.expected_labels.identity_resolution_expected
    if not expected:
        return 1.0
    expected_abstain = bool(expected.get("expected_abstain", False))
    actual_ambiguous = transcript.caller_identity.confidence < 0.5 or not (
        transcript.caller_identity.crm_id or transcript.caller_identity.phone_number_hash or transcript.caller_identity.stated_name
    )
    if expected_abstain:
        return 1.0 if actual_ambiguous else 0.0
    expected_crm_id = str(expected.get("crm_id", ""))
    if expected_crm_id:
        return 1.0 if transcript.caller_identity.crm_id == expected_crm_id and not actual_ambiguous else 0.0
    return 1.0 if not actual_ambiguous else 0.0


def _provenance_coverage(records: Sequence[Dict[str, Any]]) -> float:
    selected = 0
    with_provenance = 0
    for record in records:
        result = record.get("result", {})
        for memory in result.get("selected_memories", []):
            selected += 1
            if memory.get("evidence"):
                with_provenance += 1
    if selected == 0:
        return 1.0
    return with_provenance / float(selected)


def _aggregate_results(results: Sequence[TranscriptEvaluationResult]) -> Dict[str, float]:
    metric_names = sorted({name for result in results for name in result.metrics})
    summary: Dict[str, float] = {}
    for name in metric_names:
        values = [result.metrics[name] for result in results if name in result.metrics]
        if values:
            summary[name] = sum(values) / float(len(values))
    return summary


def _diagnostics(controller: MemoryController, retrieval_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "episodes": len(controller.store.episodes),
        "candidates": len(controller.store.candidates),
        "accepted_facts": len(controller.store.facts),
        "events": len(controller.store.events),
        "rejected_candidates": [
            {
                "id": candidate.id,
                "claim": candidate.claim,
                "action": candidate.recommended_action,
                "quarantine_reason": candidate.metadata.get("quarantine_reason", ""),
            }
            for candidate in controller.store.candidates.values()
            if candidate.recommended_action in ("ignore", "ask_consent")
            or candidate.metadata.get("quarantine_reason")
        ],
        "retrieval_checks": [
            {
                "query": item["query"],
                "answer": item["answer"],
                "passed": item["passed"],
                "abstain_reason": item.get("abstain_reason", ""),
            }
            for item in retrieval_records
        ],
    }


def _failures_from_metrics(metrics: Dict[str, float]) -> List[str]:
    failures = []
    for key, value in metrics.items():
        if key.endswith("_rate") and value > 0:
            failures.append(key)
        elif key.endswith("_accuracy") and value < 1:
            failures.append(key)
        elif key.endswith("_precision") and value < 1:
            failures.append(key)
        elif key.endswith("_recall") and value < 1:
            failures.append(key)
    return failures


def _checks(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    checks = []
    for item in value:
        if isinstance(item, dict):
            checks.append(dict(item))
        else:
            checks.append({"query": str(item), "expected_abstain": True})
    return checks


def _strings(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    normalized_text = " ".join(tokenize(text))
    lowered = text.lower()
    for term in terms:
        term_tokens = tokenize(term)
        if term_tokens and term_tokens <= tokenize(text):
            return True
        if term.lower() in lowered:
            return True
        if normalized_text and " ".join(term_tokens) in normalized_text:
            return True
    return False


def _contains_all(text: str, terms: Sequence[str]) -> bool:
    return all(_contains_any(text, [term]) for term in terms)
