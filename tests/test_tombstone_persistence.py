"""Tombstone durability: erasure state must survive restarts and restores.

The read-side registries (erased_terms/restricted_terms) are process memory.
Without persistence, a restart empties them and a restore from a pre-erasure
backup silently resurrects erased data while the audit log still shows a valid
erasure certificate. These tests pin the two rebuild paths: the sqlite
tombstones table and audit-log replay, plus the explicit reconcile sweep for
the restore case.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cognitive_memory.adapters.sqlite_backend import SqliteBackend
from cognitive_memory.models import tokenize
from cognitive_memory.reliability import GovernedMemory, NaiveBackend, Scope

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


class SqliteTombstoneTableTests(unittest.TestCase):
    def test_record_and_list_tombstones(self):
        backend = SqliteBackend(":memory:")
        backend.record_tombstone("erased", "t", "secret99")
        self.assertIn(("erased", "t", "secret99"), backend.list_tombstones())

    def test_tombstones_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            backend = SqliteBackend(db)
            backend.record_tombstone("restricted", "t", "codename")
            backend.close()
            reopened = SqliteBackend(db)
            self.assertIn(("restricted", "t", "codename"), reopened.list_tombstones())

    def test_existing_db_without_tombstone_table_upgrades(self):
        # A DB created before the tombstones table existed must open cleanly
        # and gain the table transparently (IF NOT EXISTS migration).
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "legacy.db")
            conn = sqlite3.connect(db)
            conn.executescript(_OLD_SCHEMA)
            conn.commit()
            conn.close()
            backend = SqliteBackend(db)
            self.assertEqual(backend.list_tombstones(), [])
            from cognitive_memory.reliability import MemoryRecord

            backend.write(MemoryRecord("a", "r", "v", Scope(tenant="t"), text="alpha value"))
            self.assertEqual(len(backend.candidates("alpha", "t")), 1)

    def test_duplicate_tombstone_recorded_once(self):
        backend = SqliteBackend(":memory:")
        backend.record_tombstone("erased", "t", "secret99")
        backend.record_tombstone("erased", "t", "secret99")
        rows = [r for r in backend.list_tombstones() if r == ("erased", "t", "secret99")]
        self.assertEqual(len(rows), 1)


class TombstoneRestartTests(unittest.TestCase):
    def test_forget_writes_backend_tombstone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            mem = GovernedMemory(backend=SqliteBackend(db))
            mem.remember("bob secret99 note", subject="bob", relation="note",
                         object="secret99", tenant="t", entity="bob")
            mem.forget("secret99", Scope(tenant="t", subject="bob"))
            self.assertIn(("erased", "t", "secret99"), mem.backend.list_tombstones())

    def test_restart_rebuilds_erased_terms_from_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            mem = GovernedMemory(backend=SqliteBackend(db))
            mem.forget("secret99", Scope(tenant="t"))
            mem2 = GovernedMemory(backend=SqliteBackend(db))  # restart
            self.assertIn(tokenize("secret99"), mem2.erased_terms.get("t", []))

    def test_restrict_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            mem = GovernedMemory(backend=SqliteBackend(db))
            mem.remember("project codename is zeus", subject="project", relation="codename",
                         object="zeus", tenant="t", entity="project")
            mem.restrict("zeus", Scope(tenant="t"))
            mem2 = GovernedMemory(backend=SqliteBackend(db))  # restart
            result = mem2.recall_value("project codename zeus", tenant="t", entity="project")
            self.assertTrue(result.abstained, "restricted term served after restart")
            self.assertIn("do_not_use", {reason for _, reason in result.excluded})

    def test_reingest_after_restart_is_quarantined(self):
        # The full loop: forget, restart, the erased value arrives again. The
        # rebuilt registry must catch it on the write path, not just at read.
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            mem = GovernedMemory(backend=SqliteBackend(db))
            mem.forget("secret99", Scope(tenant="t"))
            mem2 = GovernedMemory(backend=SqliteBackend(db))  # restart
            mem2.remember("bob secret99 note", subject="bob", relation="note",
                          object="secret99", tenant="t", entity="bob")
            result = mem2.recall_value("bob note secret99", tenant="t", entity="bob")
            self.assertTrue(result.abstained)
            reasons = [e.details.get("reason") for e in mem2.audit.filter("quarantine")]
            self.assertIn("erased_term_reingest", reasons)

    def test_restore_from_pre_erasure_backup_is_blocked_read_side(self):
        # The auditors' scenario: the store is restored from a backup taken
        # before the erasure. The restored DB has the row and no tombstone;
        # only the persisted audit log knows about the erasure.
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            backup = str(Path(tmp) / "mem_backup.db")
            audit = str(Path(tmp) / "audit.jsonl")
            mem = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            mem.remember("alex health note secret123", subject="alex", relation="health_note",
                         object="secret123", tenant="t", entity="alex")
            mem.backend.close()
            shutil.copyfile(db, backup)
            mem = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            mem.forget("secret123", Scope(tenant="t", subject="alex"))
            # restore: fresh process over the pre-erasure copy, same audit log
            restored = GovernedMemory(backend=SqliteBackend(backup), audit_path=audit)
            result = restored.recall_value("alex health note secret123", tenant="t", entity="alex")
            self.assertTrue(result.abstained, "erased data served after restore from backup")


class AuditReplayTests(unittest.TestCase):
    def test_replay_from_audit_log_with_nondurable_backend(self):
        # In-memory backend, persisted audit: a process restart loses the
        # registry but the audit log rebuilds it.
        with tempfile.TemporaryDirectory() as tmp:
            audit = str(Path(tmp) / "audit.jsonl")
            mem = GovernedMemory(NaiveBackend(), audit_path=audit)
            mem.forget("secret99", Scope(tenant="t"))
            mem2 = GovernedMemory(NaiveBackend(), audit_path=audit)  # restart
            self.assertIn(tokenize("secret99"), mem2.erased_terms.get("t", []))

    def test_replay_deduplicates_backend_and_audit_sources(self):
        # sqlite tombstones and audit entries both name the same erasure; the
        # rebuilt registry must hold it once.
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            audit = str(Path(tmp) / "audit.jsonl")
            mem = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            mem.forget("secret99", Scope(tenant="t"))
            mem2 = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            hits = [tokens for tokens in mem2.erased_terms.get("t", []) if tokens == tokenize("secret99")]
            self.assertEqual(len(hits), 1)


class ReconcileTests(unittest.TestCase):
    def test_reconcile_redeletes_restored_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mem.db")
            backup = str(Path(tmp) / "mem_backup.db")
            audit = str(Path(tmp) / "audit.jsonl")
            mem = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            mem.remember("alex health note secret123", subject="alex", relation="health_note",
                         object="secret123", tenant="t", entity="alex")
            mem.backend.close()
            shutil.copyfile(db, backup)
            mem = GovernedMemory(backend=SqliteBackend(db), audit_path=audit)
            mem.forget("secret123", Scope(tenant="t", subject="alex"))
            restored = GovernedMemory(backend=SqliteBackend(backup), audit_path=audit)
            removed = restored.reconcile_tombstones()
            self.assertGreaterEqual(removed, 1)
            texts = [r.text for r in restored.backend.all_records()]
            self.assertNotIn("alex health note secret123", texts)
            self.assertTrue(restored.audit.filter("tombstone_reconcile"))

    def test_reconcile_is_tenant_scoped(self):
        mem = GovernedMemory(backend=SqliteBackend(":memory:"))
        mem.remember("alex health note secret123", subject="alex", relation="health_note",
                     object="secret123", tenant="a", entity="alex")
        mem.remember("bora health note secret123", subject="bora", relation="health_note",
                     object="secret123", tenant="b", entity="bora")
        mem.forget("secret123", Scope(tenant="a"))
        # tenant a re-acquires a matching record after the forget
        mem.remember("alex health note secret123", subject="alex", relation="health_note",
                     object="secret123", tenant="a", entity="alex")
        mem.reconcile_tombstones()
        tenants = {r.scope.tenant for r in mem.backend.all_records() if "secret123" in r.text}
        self.assertEqual(tenants, {"b"}, "reconcile must never cross tenants")


if __name__ == "__main__":
    unittest.main()
