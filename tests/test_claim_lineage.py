"""Claim lineage: accumulate who asserted what instead of overwriting.

TemporalFact always had edge fields (supersedes/conflict_with/evidence) but
each fact kept exactly ONE source and the same-object merge overwrote it,
losing corroboration history. MemoryRecord had no lineage at all. These
fields are additive with empty defaults, so every store round-trips them and
old artifacts load unchanged; default conflict resolution never reads them.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cognitive_memory.adapters.sqlite_backend import SqliteBackend
from cognitive_memory.reliability import GovernedMemory, MemoryRecord, Scope

_OLD_SCHEMA = """
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
"""


class LineageFieldTests(unittest.TestCase):
    def test_memory_record_defaults_empty(self):
        record = MemoryRecord("s", "r", "o", Scope())
        self.assertEqual(record.assertions, [])
        self.assertEqual(record.supersedes_ids, [])
        self.assertEqual(record.contradicts_ids, [])

    def test_remember_seeds_own_assertion(self):
        mem = GovernedMemory()
        mem.remember("alice salary 120k", subject="alice", relation="salary",
                     object="120k", tenant="t", entity="alice", source="crm", trust=0.8)
        record = mem.backend.all_records()[0]
        self.assertEqual(record.assertions, [{"source": "crm", "trust": 0.8, "valid_at": 1}])

    def test_lineage_fields_survive_restart_and_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "legacy.db")
            conn = sqlite3.connect(db)
            conn.executescript(_OLD_SCHEMA)
            conn.execute(
                "INSERT INTO records (id, tenant, subject, relation, object, text) "
                "VALUES ('old1', 't', 'a', 'r', 'v', 'a r v')"
            )
            conn.commit()
            conn.close()
            backend = SqliteBackend(db)  # guarded ALTER adds the columns
            legacy = [r for r in backend.all_records() if r.id == "old1"][0]
            self.assertEqual(legacy.assertions, [])
            rid = backend.write(MemoryRecord(
                "b", "r", "w", Scope(tenant="t"), text="b r w",
                assertions=[{"source": "user", "trust": 0.9, "valid_at": 3}],
                supersedes_ids=["x1"], contradicts_ids=["y2"],
            ))
            reopened = SqliteBackend(db)
            got = [r for r in reopened.all_records() if r.id == rid][0]
            self.assertEqual(got.assertions, [{"source": "user", "trust": 0.9, "valid_at": 3}])
            self.assertEqual(got.supersedes_ids, ["x1"])
            self.assertEqual(got.contradicts_ids, ["y2"])

    def test_same_object_merge_accumulates_assertions_and_keeps_source_upgrade(self):
        from datetime import datetime, timezone

        from cognitive_memory.controller import MemoryController
        from cognitive_memory.models import Episode

        controller = MemoryController()
        controller.ingest_episode(Episode("FACT user|work_mode|remote",
                                          timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        controller.ingest_episode(Episode("FACT user|work_mode|remote", source="verified_crm",
                                          timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        fact = [f for f in controller.store.list_facts() if f.object == "remote"][0]
        self.assertEqual(len(fact.assertions), 2,
                         "corroboration history lost: merge overwrote instead of accumulating")
        self.assertEqual({a["source_type"] for a in fact.assertions},
                         {"user_statement", "tool_record"})
        # the existing source upgrade is pinned: highest-rank source wins
        self.assertEqual(fact.source_trust, "authoritative")


if __name__ == "__main__":
    unittest.main()
