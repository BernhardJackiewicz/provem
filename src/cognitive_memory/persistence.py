from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    Episode,
    EventContext,
    EventParticipant,
    EventRelation,
    ExcludedMemory,
    MemoryCandidate,
    MemoryEvent,
    Reflection,
    RetrievalResult,
    SelectedMemory,
    TemporalFact,
    iso,
)
from .policy import PolicyStore
from .store import InMemoryStore


SNAPSHOT_VERSION = 1


@dataclass
class MemorySnapshot:
    store: InMemoryStore
    policy: PolicyStore
    retrieval_traces: List[Dict[str, Any]]


def save_snapshot(
    path: str,
    store: InMemoryStore,
    policy: PolicyStore,
    retrieval_traces: Optional[Iterable[Dict[str, Any]]] = None,
) -> None:
    """Write an inspectable JSONL snapshot.

    This is local research persistence, not a production database. Records are
    line-delimited so snapshots are easy to diff and inspect without extra
    dependencies.
    """

    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    traces = list(retrieval_traces if retrieval_traces is not None else store.retrieval_traces)
    records = [
        {"type": "metadata", "version": SNAPSHOT_VERSION},
        {"type": "policy", "data": policy.to_dict()},
    ]
    records.extend({"type": "episode", "data": item.to_dict()} for item in store.list_episodes())
    records.extend({"type": "candidate", "data": item.to_dict()} for item in sorted(store.candidates.values(), key=lambda item: item.created_at))
    records.extend({"type": "fact", "data": item.to_dict()} for item in store.list_facts())
    records.extend({"type": "event", "data": item.to_dict()} for item in store.list_events())
    records.extend({"type": "reflection", "data": item.to_dict()} for item in store.list_reflections())
    records.extend({"type": "store_audit", "data": dict(item)} for item in store.audit_log)
    records.extend({"type": "retrieval_trace", "data": _trace_to_dict(item)} for item in traces)

    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_json_ready(record), sort_keys=True))
            handle.write("\n")


def load_snapshot(path: str) -> MemorySnapshot:
    source = Path(path)
    store = InMemoryStore()
    policy = PolicyStore()
    retrieval_traces: List[Dict[str, Any]] = []
    audit_log: List[Dict[str, str]] = []

    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            record_type = record.get("type")
            data = record.get("data", {})

            if record_type == "metadata":
                version = int(record.get("version", 0))
                if version > SNAPSHOT_VERSION:
                    raise ValueError("Unsupported memory snapshot version %s" % version)
            elif record_type == "policy":
                policy = PolicyStore.from_dict(data)
            elif record_type == "episode":
                item = _construct(Episode, data)
                store.episodes[item.id] = item
            elif record_type == "candidate":
                item = _construct(MemoryCandidate, data)
                store.candidates[item.id] = item
            elif record_type == "fact":
                item = _construct(TemporalFact, data)
                store.facts[item.id] = item
            elif record_type == "event":
                item = _event_from_dict(data)
                store.events[item.id] = item
            elif record_type == "reflection":
                item = _construct(Reflection, data)
                store.reflections[item.id] = item
            elif record_type == "store_audit":
                audit_log.append({"event": str(data.get("event", "")), "target_id": str(data.get("target_id", ""))})
            elif record_type == "retrieval_trace":
                retrieval_traces.append(dict(data))
            else:
                raise ValueError("Unsupported snapshot record type %r on line %s" % (record_type, line_number))

    store.audit_log = audit_log
    store.retrieval_traces = retrieval_traces
    return MemorySnapshot(store=store, policy=policy, retrieval_traces=retrieval_traces)


def retrieval_trace_record(result: RetrievalResult, query: str = "") -> Dict[str, Any]:
    data = result.to_dict()
    if query:
        data["query"] = query
    data["answer"] = result.answer_text()
    return data


def retrieval_result_from_dict(data: Dict[str, Any]) -> RetrievalResult:
    return RetrievalResult(
        selected_memories=[_construct(SelectedMemory, item) for item in data.get("selected_memories", [])],
        excluded_memories=[_construct(ExcludedMemory, item) for item in data.get("excluded_memories", [])],
        provenance=list(data.get("provenance", [])),
        confidence=float(data.get("confidence", 0.0)),
        abstain_recommended=bool(data.get("abstain_recommended", False)),
        abstain_reason=str(data.get("abstain_reason", "")),
        retrieval_trace=str(data.get("retrieval_trace", "")),
    )


def _trace_to_dict(trace: Any) -> Dict[str, Any]:
    if isinstance(trace, RetrievalResult):
        return retrieval_trace_record(trace)
    return dict(trace)


def _json_ready(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return iso(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_ready(item) for item in value)
    return value


def _construct(cls: Any, data: Dict[str, Any]) -> Any:
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in allowed})


def _event_from_dict(data: Dict[str, Any]) -> MemoryEvent:
    event_data = dict(data)
    event_data["participants"] = [_construct(EventParticipant, item) for item in event_data.get("participants", [])]
    event_data["relations"] = [_construct(EventRelation, item) for item in event_data.get("relations", [])]
    event_data["context"] = _construct(EventContext, event_data.get("context", {}))
    return _construct(MemoryEvent, event_data)
