"""Tamper-evident audit trail for governed memory (enterprise/regulatory).

Every governance decision (quarantine, erasure, conflict resolution, scope
block) is appended as a structured entry linked into a SHA-256 hash chain, so
any later edit, insertion, or deletion of a past entry is detectable with
:meth:`AuditLog.verify`. Erasure produces a signed-style certificate recording
exactly what was removed and when -- the artifact a DSGVO Art. 17 / audit
request needs.

Pure stdlib. The clock is injectable so the chain is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

GENESIS_HASH = "0" * 64


def _canonical(details: Dict[str, Any]) -> str:
    return json.dumps(details, sort_keys=True, ensure_ascii=False, default=str)


def _hash_entry(seq: int, action: str, details: Dict[str, Any], timestamp: str, prev_hash: str) -> str:
    payload = "%d|%s|%s|%s|%s" % (seq, action, _canonical(details), timestamp, prev_hash)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    action: str
    details: Dict[str, Any]
    timestamp: str
    prev_hash: str
    hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "action": self.action,
            "details": dict(self.details),
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


def _default_clock() -> Callable[[], str]:
    def clock() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    return clock


class AuditLog:
    """Append-only, hash-chained governance audit trail."""

    def __init__(self, clock: Optional[Callable[[], str]] = None, persist_path: Optional[str] = None) -> None:
        self._clock = clock or _default_clock()
        self._entries: List[AuditEntry] = []
        self._persist_path = persist_path
        if persist_path:
            self._load_persisted(persist_path)

    def _load_persisted(self, path: str) -> None:
        """Rebuild the chain from an existing append-only JSONL file, so the
        audit trail survives a restart (subsequent records chain onto it)."""
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                # Tolerate a truncated/corrupt trailing line (a routine crash-
                # during-append artifact); the valid prefix of the chain loads.
                continue
            self._entries.append(
                AuditEntry(
                    seq=int(raw["seq"]), action=str(raw["action"]), details=dict(raw.get("details", {})),
                    timestamp=str(raw["timestamp"]), prev_hash=str(raw["prev_hash"]), hash=str(raw["hash"]),
                )
            )

    def record(self, action: str, **details: Any) -> AuditEntry:
        seq = len(self._entries)
        prev_hash = self._entries[-1].hash if self._entries else GENESIS_HASH
        timestamp = self._clock()
        entry = AuditEntry(
            seq=seq,
            action=action,
            details=dict(details),
            timestamp=timestamp,
            prev_hash=prev_hash,
            hash=_hash_entry(seq, action, dict(details), timestamp, prev_hash),
        )
        self._entries.append(entry)
        if self._persist_path:
            with open(self._persist_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry

    # backward-compatible convenience for the old string-append call sites
    def append(self, message: str) -> AuditEntry:
        return self.record("log", message=str(message))

    def erasure_certificate(
        self, term: str, removed_ids: List[str], tenant: str, backend_confirmed: int
    ) -> AuditEntry:
        return self.record(
            "erasure",
            term=term,
            tenant=tenant,
            targeted_count=len(removed_ids),
            backend_confirmed_deletes=backend_confirmed,
            removed_ids=list(removed_ids),
            note="read-side erasure is enforced regardless of backend delete outcome",
        )

    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def filter(self, action: str) -> List[AuditEntry]:
        return [e for e in self._entries if e.action == action]

    def verify(self, expected_count: Optional[int] = None, expected_head: Optional[str] = None) -> bool:
        """Recompute the chain; return False if any entry was altered/reordered.

        A pure hash chain cannot by itself detect *truncation* of trailing
        entries (the surviving prefix stays internally consistent). To catch
        silent history deletion, anchor the log: persist ``head_hash`` and
        ``count`` after each append and pass them here (or to
        :func:`verify_export`). Publishing the head hash externally is the
        standard tamper-evidence pattern.
        """
        prev_hash = GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.seq != i or entry.prev_hash != prev_hash:
                return False
            expected = _hash_entry(entry.seq, entry.action, entry.details, entry.timestamp, entry.prev_hash)
            if expected != entry.hash:
                return False
            prev_hash = entry.hash
        if expected_count is not None and len(self._entries) != expected_count:
            return False
        if expected_head is not None and prev_hash != expected_head:
            return False
        return True

    def head_hash(self) -> str:
        return self._entries[-1].hash if self._entries else GENESIS_HASH

    def to_dict(self) -> Dict[str, Any]:
        return {
            "genesis": GENESIS_HASH,
            "count": len(self._entries),
            "head_hash": self._entries[-1].hash if self._entries else GENESIS_HASH,
            "entries": [e.to_dict() for e in self._entries],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)


def verify_export(data: Dict[str, Any], expected_count: Optional[int] = None, expected_head: Optional[str] = None) -> bool:
    """Verify an exported audit dict (e.g. loaded from disk at another site).

    The export carries its own ``count`` and ``head_hash``; these are checked
    for internal consistency (catching truncation *within* a single export).
    Pass ``expected_count``/``expected_head`` from an independent anchor to also
    detect truncation of a whole trailing export.
    """
    entries = data.get("entries", [])
    prev_hash = GENESIS_HASH
    for i, raw in enumerate(entries):
        if int(raw.get("seq", -1)) != i or raw.get("prev_hash") != prev_hash:
            return False
        expected = _hash_entry(
            int(raw["seq"]), str(raw["action"]), dict(raw.get("details", {})), str(raw["timestamp"]), str(raw["prev_hash"])
        )
        if expected != raw.get("hash"):
            return False
        prev_hash = str(raw["hash"])
    # self-consistency: the export's own count/head must match its entries
    if "count" in data and int(data["count"]) != len(entries):
        return False
    if "head_hash" in data and str(data["head_hash"]) != prev_hash:
        return False
    if expected_count is not None and len(entries) != expected_count:
        return False
    if expected_head is not None and prev_hash != expected_head:
        return False
    return True
