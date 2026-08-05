"""Durable MemoryBackend on stdlib sqlite3 (no external dependency).

Same contract and ranking semantics as the in-memory backends, but records
survive a restart. Erasure is durable: ``delete_ids`` removes rows. Ranking uses
the shared coverage-blended BM25 (``ranking.blended_bm25_candidates``), so
governed behaviour is identical to :class:`Bm25Backend`.

Single writer, tenant-agnostic storage (tenant is a column); the governance
wrapper scopes reads by tenant and keeps erasure tenant-keyed, so one shared
backend safely serves many tenants.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Sequence, Tuple

from ..models import tokenize  # noqa: F401  (kept for parity/imports elsewhere)
from ..ranking import blended_bm25_candidates
from ..reliability import MemoryRecord, Scope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    scope_subject TEXT NOT NULL DEFAULT '',
    scope_session TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    relation TEXT NOT NULL DEFAULT '',
    object TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',
    trust REAL NOT NULL DEFAULT 0.9,
    provenance TEXT NOT NULL DEFAULT '',
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT NOT NULL DEFAULT '',
    valid_at INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_tenant ON records(tenant);
CREATE TABLE IF NOT EXISTS tombstones (
    kind TEXT NOT NULL,
    tenant TEXT NOT NULL,
    term TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(kind, tenant, term)
);
CREATE INDEX IF NOT EXISTS idx_tombstones_tenant ON tombstones(tenant);
CREATE TABLE IF NOT EXISTS record_purposes (
    record_id TEXT PRIMARY KEY,
    allowed TEXT NOT NULL DEFAULT '[]',
    consented TEXT NOT NULL DEFAULT '[]'
);
"""

_SELECT_WITH_PURPOSES = (
    "SELECT r.*, p.allowed AS p_allowed, p.consented AS p_consented "
    "FROM records r LEFT JOIN record_purposes p ON p.record_id = r.id"
)


class SqliteBackend:
    """Durable, tenant-scoped MemoryBackend backed by SQLite."""

    def __init__(self, path: str = ":memory:", k1: float = 1.5, b: float = 0.75, norm_k: float = 1.0) -> None:
        self.path = path
        self.k1 = k1
        self.b = b
        self.norm_k = norm_k
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._counter = self._max_counter()

    def _max_counter(self) -> int:
        # Seed from the highest existing auto-id ('s<N>'), NOT COUNT(*): after a
        # delete+reload, COUNT would let the next id collide with a live row and
        # INSERT OR REPLACE would silently overwrite it (data loss).
        cur = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 2) AS INTEGER)) AS m FROM records "
            "WHERE id LIKE 's%' AND SUBSTR(id, 2) GLOB '[0-9]*'"
        )
        row = cur.fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def write(self, record: MemoryRecord) -> str:
        self._counter += 1
        record.id = record.id or "s%d" % self._counter
        self._conn.execute(
            "INSERT OR REPLACE INTO records (id, tenant, scope_subject, scope_session, subject, relation, "
            "object, text, source, trust, provenance, quarantined, quarantine_reason, valid_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.id, record.scope.tenant, record.scope.subject, record.scope.session,
                record.subject, record.relation, record.object, record.text, record.source,
                float(record.trust), record.provenance, 1 if record.quarantined else 0,
                record.quarantine_reason, int(record.valid_at), getattr(record, "created_at", "") or "",
            ),
        )
        # Purpose metadata lives in an additive side table so legacy DB files
        # keep working untouched (missing row = unrestricted).
        if record.allowed_purposes or record.consented_purposes:
            self._conn.execute(
                "INSERT OR REPLACE INTO record_purposes (record_id, allowed, consented) VALUES (?,?,?)",
                (record.id, json.dumps(list(record.allowed_purposes)),
                 json.dumps(list(record.consented_purposes))),
            )
        else:
            self._conn.execute("DELETE FROM record_purposes WHERE record_id = ?", (record.id,))
        self._conn.commit()
        return record.id

    def delete_ids(self, ids: Sequence[str]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute("DELETE FROM records WHERE id IN (%s)" % placeholders, ids)
        self._conn.execute("DELETE FROM record_purposes WHERE record_id IN (%s)" % placeholders, ids)
        self._conn.commit()
        return cur.rowcount

    def record_tombstone(self, kind: str, tenant: str, term: str, created_at: str = "") -> None:
        """Persist an erasure/restriction tombstone in the same DB file, so a
        backup of the store carries its own deletion state (a restore cannot
        silently roll back erasures)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO tombstones (kind, tenant, term, created_at) VALUES (?,?,?,?)",
            (kind, tenant, term, created_at),
        )
        self._conn.commit()

    def list_tombstones(self) -> List[Tuple[str, str, str]]:
        cur = self._conn.execute("SELECT kind, tenant, term FROM tombstones")
        return [(row["kind"], row["tenant"], row["term"]) for row in cur.fetchall()]

    @staticmethod
    def _purposes_from(row: sqlite3.Row, key: str) -> Tuple[str, ...]:
        try:
            raw = row[key]
        except (IndexError, KeyError):
            return ()
        if not raw:
            return ()
        try:
            return tuple(str(v) for v in json.loads(raw))
        except (ValueError, TypeError):
            return ()

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        record = MemoryRecord(
            subject=row["subject"], relation=row["relation"], object=row["object"],
            scope=Scope(tenant=row["tenant"], subject=row["scope_subject"], session=row["scope_session"]),
            text=row["text"], source=row["source"], trust=row["trust"],
            provenance=row["provenance"], quarantined=bool(row["quarantined"]),
            quarantine_reason=row["quarantine_reason"], valid_at=int(row["valid_at"]), id=row["id"],
            allowed_purposes=self._purposes_from(row, "p_allowed"),
            consented_purposes=self._purposes_from(row, "p_consented"),
        )
        # created_at exists once F4 adds it to MemoryRecord; set if attribute present
        if hasattr(record, "created_at"):
            try:
                record.created_at = row["created_at"]
            except Exception:
                pass
        return record

    def all_records(self) -> List[MemoryRecord]:
        cur = self._conn.execute(_SELECT_WITH_PURPOSES)
        return [self._row_to_record(r) for r in cur.fetchall()]

    def candidates(self, query: str, tenant: str) -> List[Tuple[float, MemoryRecord]]:
        cur = self._conn.execute(_SELECT_WITH_PURPOSES + " WHERE r.tenant = ?", (tenant,))
        records = [self._row_to_record(r) for r in cur.fetchall()]
        return blended_bm25_candidates(records, query, k1=self.k1, b=self.b, norm_k=self.norm_k)

    def close(self) -> None:
        self._conn.close()
