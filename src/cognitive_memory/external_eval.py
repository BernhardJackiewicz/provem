from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .transcript_eval import (
    Transcript,
    TranscriptRedactor,
    evaluate_transcript_collection,
)


SAFE_PII_STATUSES = {"fake", "synthetic", "anonymized", "redacted", "deidentified", "no_pii"}


class ExternalValidationError(ValueError):
    """Raised when an external validation manifest is unsafe or unsupported."""


@dataclass
class ExternalDatasetConfig:
    dataset_name: str
    source: str
    license: str
    pii_status: str
    local_path: str
    approved_for_eval: bool
    expected_schema: str
    language: str = "unknown"
    domain: str = "generic"
    real_vs_synthetic: str = "unknown"
    notes: str = ""
    loader: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExternalDatasetConfig":
        return cls(
            dataset_name=str(data.get("dataset_name", "")),
            source=str(data.get("source", "")),
            license=str(data.get("license", "")),
            language=str(data.get("language", "unknown")),
            domain=str(data.get("domain", "generic")),
            real_vs_synthetic=str(data.get("real_vs_synthetic", "unknown")),
            pii_status=str(data.get("pii_status", "")),
            local_path=str(data.get("local_path", "")),
            approved_for_eval=bool(data.get("approved_for_eval", False)),
            notes=str(data.get("notes", "")),
            expected_schema=str(data.get("expected_schema", "")),
            loader=str(data.get("loader", "")),
        )


@dataclass
class ValidationManifest:
    datasets: List[ExternalDatasetConfig] = field(default_factory=list)
    manifest_path: str = ""

    @classmethod
    def from_path(cls, manifest_path: str) -> "ValidationManifest":
        path = Path(manifest_path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "datasets" in payload:
            raw_datasets = payload.get("datasets", [])
        elif isinstance(payload, list):
            raw_datasets = payload
        elif isinstance(payload, dict):
            raw_datasets = [payload]
        else:
            raise ExternalValidationError("Validation manifest must be a dataset object, list, or object with datasets.")
        datasets = [ExternalDatasetConfig.from_dict(dict(item)) for item in raw_datasets]
        return cls(datasets=datasets, manifest_path=str(path))

    def validate_for_eval(self) -> None:
        if not self.datasets:
            raise ExternalValidationError("Validation manifest has no datasets.")
        base = Path(self.manifest_path).parent
        for dataset in self.datasets:
            _validate_dataset_config(dataset, base)


class GenericTranscriptJsonlLoader:
    """Load JSONL records and map them into the existing transcript schema."""

    def load(self, path: str, config: ExternalDatasetConfig) -> List[Transcript]:
        transcripts: List[Transcript] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                transcripts.append(_transcript_from_external_record(json.loads(stripped), config))
        return transcripts


class GenericTranscriptJsonLoader:
    """Load JSON records and map them into the existing transcript schema."""

    def load(self, path: str, config: ExternalDatasetConfig) -> List[Transcript]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "transcripts" in payload:
            records = payload.get("transcripts", [])
        elif isinstance(payload, list):
            records = payload
        else:
            records = [payload]
        return [_transcript_from_external_record(dict(record), config) for record in records]


class PublicDatasetLoaderStub:
    """Placeholder for future dataset-specific adapters.

    This is intentionally not wired to downloads or vendor APIs. Public datasets
    must be reviewed, downloaded manually outside the repo, and mapped through a
    concrete loader later.
    """

    def load(self, path: str, config: ExternalDatasetConfig) -> List[Transcript]:
        raise ExternalValidationError(
            "PublicDatasetLoaderStub is not a real loader for %s; add a reviewed dataset-specific mapper."
            % config.dataset_name
        )


def load_validation_manifest(manifest_path: str) -> ValidationManifest:
    manifest = ValidationManifest.from_path(manifest_path)
    manifest.validate_for_eval()
    return manifest


def evaluate_external_manifest(manifest_path: str) -> Dict[str, Any]:
    manifest = load_validation_manifest(manifest_path)
    base = Path(manifest.manifest_path).parent
    dataset_reports = []
    combined_results = []
    redaction_terms: List[str] = []
    transcript_count = 0
    labeled_count = 0

    for dataset in manifest.datasets:
        local_path = _resolved_local_path(dataset, base)
        transcripts = _loader_for(dataset, local_path).load(str(local_path), dataset)
        report = evaluate_transcript_collection(transcripts)
        transcript_count += int(report["transcript_count"])
        labeled_count += int(report["labeled_count"])
        combined_results.extend(report["results"])
        redaction_terms.extend(report.get("redaction_terms", []))
        dataset_reports.append(
            {
                "dataset_name": dataset.dataset_name,
                "source": dataset.source,
                "license": dataset.license,
                "language": dataset.language,
                "domain": dataset.domain,
                "real_vs_synthetic": dataset.real_vs_synthetic,
                "pii_status": dataset.pii_status,
                "local_path_name": local_path.name,
                "expected_schema": dataset.expected_schema,
                "transcript_count": report["transcript_count"],
                "labeled_count": report["labeled_count"],
                "summary": report["summary"],
            }
        )

    return {
        "manifest": Path(manifest_path).name,
        "dataset_count": len(manifest.datasets),
        "evaluated_dataset_count": len(dataset_reports),
        "transcript_count": transcript_count,
        "labeled_count": labeled_count,
        "summary": _aggregate_result_dicts(combined_results),
        "datasets": dataset_reports,
        "results": combined_results,
        "redaction_terms": sorted({term for term in redaction_terms if term}),
    }


def dumps_external_report(
    report: Dict[str, Any],
    as_json: bool = False,
    redaction_terms: Optional[Sequence[str]] = None,
    redact_salaries: bool = False,
    redact_companies: bool = False,
) -> str:
    terms = list(redaction_terms or []) + [str(term) for term in report.get("redaction_terms", [])]
    safe_report = dict(report)
    safe_report.pop("redaction_terms", None)
    redactor = TranscriptRedactor(
        terms=terms,
        redact_salaries=redact_salaries,
        redact_companies=redact_companies,
    )
    safe_report = redactor.redact(safe_report)
    if as_json:
        return json.dumps(safe_report, indent=2, sort_keys=True)

    lines = [
        "External transcript evaluation",
        "manifest: %s" % safe_report.get("manifest", ""),
        "datasets: %s evaluated: %s"
        % (safe_report.get("dataset_count", 0), safe_report.get("evaluated_dataset_count", 0)),
        "transcripts: %s labeled: %s" % (safe_report.get("transcript_count", 0), safe_report.get("labeled_count", 0)),
    ]
    for key, value in sorted(safe_report.get("summary", {}).items()):
        if isinstance(value, float):
            lines.append("%s: %.4f" % (key, value))
    for dataset in safe_report.get("datasets", []):
        lines.append(
            "%s [%s/%s] transcripts=%s labeled=%s"
            % (
                dataset.get("dataset_name", ""),
                dataset.get("domain", ""),
                dataset.get("pii_status", ""),
                dataset.get("transcript_count", 0),
                dataset.get("labeled_count", 0),
            )
        )
    return "\n".join(lines)


def _validate_dataset_config(dataset: ExternalDatasetConfig, base: Path) -> None:
    if not dataset.dataset_name:
        raise ExternalValidationError("Dataset manifest entry is missing dataset_name.")
    if not dataset.approved_for_eval:
        raise ExternalValidationError("Dataset %s is not approved_for_eval." % dataset.dataset_name)
    if not dataset.license.strip():
        raise ExternalValidationError("Dataset %s is missing license metadata." % dataset.dataset_name)
    if dataset.pii_status.lower() not in SAFE_PII_STATUSES:
        raise ExternalValidationError(
            "Dataset %s has unsafe or unknown pii_status: %s" % (dataset.dataset_name, dataset.pii_status)
        )
    if not dataset.local_path:
        raise ExternalValidationError("Dataset %s is missing local_path." % dataset.dataset_name)
    local_path = _resolved_local_path(dataset, base)
    if not local_path.exists():
        raise ExternalValidationError("Dataset %s local_path does not exist." % dataset.dataset_name)
    if not dataset.expected_schema:
        raise ExternalValidationError("Dataset %s is missing expected_schema." % dataset.dataset_name)


def _resolved_local_path(dataset: ExternalDatasetConfig, base: Path) -> Path:
    local_path = Path(dataset.local_path)
    if not local_path.is_absolute():
        local_path = base / local_path
    return local_path.resolve()


def _loader_for(dataset: ExternalDatasetConfig, local_path: Path) -> Any:
    loader = dataset.loader.lower() or dataset.expected_schema.lower()
    if loader in ("public_dataset_stub", "public_stub"):
        return PublicDatasetLoaderStub()
    if loader in ("generic_transcript_jsonl", "transcript_jsonl", "jsonl"):
        return GenericTranscriptJsonlLoader()
    if loader in ("generic_transcript_json", "transcript_json", "json"):
        return GenericTranscriptJsonLoader()
    if local_path.suffix.lower() == ".jsonl":
        return GenericTranscriptJsonlLoader()
    if local_path.suffix.lower() == ".json":
        return GenericTranscriptJsonLoader()
    raise ExternalValidationError("No loader for dataset %s schema %s." % (dataset.dataset_name, dataset.expected_schema))


def _transcript_from_external_record(record: Dict[str, Any], config: ExternalDatasetConfig) -> Transcript:
    if "turns" in record and "transcript_id" in record:
        payload = dict(record)
    else:
        payload = _map_generic_record(record, config)
    return Transcript.from_dict(payload)


def _map_generic_record(record: Dict[str, Any], config: ExternalDatasetConfig) -> Dict[str, Any]:
    turns = record.get("turns") or record.get("messages") or []
    mapped_turns = []
    participants = {}
    for index, turn in enumerate(turns):
        speaker = str(turn.get("speaker") or turn.get("speaker_id") or turn.get("role") or "speaker_%03d" % (index + 1))
        role = str(turn.get("role", "unknown"))
        participants[speaker] = role
        mapped_turns.append(
            {
                "speaker": speaker,
                "text": str(turn.get("text") or turn.get("content") or ""),
                "timestamp": turn.get("timestamp"),
                "asr_confidence": turn.get("asr_confidence"),
            }
        )
    return {
        "transcript_id": str(record.get("transcript_id") or record.get("id") or record.get("conversation_id") or ""),
        "domain": str(record.get("domain") or config.domain or "generic"),
        "timestamp": str(record.get("timestamp") or record.get("created_at") or "1970-01-01T00:00:00"),
        "caller_identity": dict(record.get("caller_identity", {})),
        "participants": [
            {"speaker_id": speaker, "role": role}
            for speaker, role in sorted(participants.items())
        ],
        "turns": mapped_turns,
        "existing_context": dict(record.get("existing_context", {})),
        "expected_labels": dict(record.get("expected_labels") or record.get("labels") or {}),
        "redaction_terms": [str(term) for term in record.get("redaction_terms", [])],
    }


def _aggregate_result_dicts(results: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    metric_names = sorted({name for result in results for name in result.get("metrics", {})})
    summary: Dict[str, float] = {}
    for name in metric_names:
        values = [float(result["metrics"][name]) for result in results if name in result.get("metrics", {})]
        if values:
            summary[name] = sum(values) / float(len(values))
    return summary


def manifest_to_dict(manifest: ValidationManifest) -> Dict[str, Any]:
    return {
        "manifest_path": manifest.manifest_path,
        "datasets": [asdict(dataset) for dataset in manifest.datasets],
    }
