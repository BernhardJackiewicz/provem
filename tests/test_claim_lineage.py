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


class LineageResolutionTests(unittest.TestCase):
    """Opt-in conflict_resolution='lineage': corroboration counts before the
    scalar trust margin. Default behaviour stays byte-identical."""

    LINEAGE = {"name": "lineage", "conflict_resolution": "lineage"}

    def _fact(self, mem, obj, source="user", trust=0.9):
        from cognitive_memory.reliability import IngestTurn

        scope = Scope("t", "alex_1")
        mem.ingest(IngestTurn("fact", "alex salary %s" % obj, "alex_1", "salary", obj,
                              scope, source, trust))

    def _recall(self, mem):
        from cognitive_memory.reliability import QueryTurn

        return mem.recall(QueryTurn("alex salary", Scope("t", "alex_1"), None))

    def test_default_policy_conflict_behavior_pinned(self):
        # byte-compat pin of the existing PoisoningTests fixtures under an
        # explicitly constructed default-valued policy
        mem = GovernedMemory(policy={"name": "default-pin"})
        self._fact(mem, "120k", "user", 0.96)
        self._fact(mem, "200k", "external", 0.5)
        resolved = self._recall(mem)
        self.assertEqual(resolved.answer, "120k")
        abstain_mem = GovernedMemory(policy={"name": "default-pin"})
        self._fact(abstain_mem, "120k", "user", 0.96)
        self._fact(abstain_mem, "200k", "external", 0.9)
        abstained = self._recall(abstain_mem)
        self.assertTrue(abstained.abstained)
        self.assertEqual(abstained.reason, "source_conflict")

    def test_corroborated_claim_beats_single_fresh_same_channel_assertion(self):
        # the same-channel MINJA case with history: the true value is asserted
        # by two independent sources; the poison arrives later through the
        # trusted user channel at equal trust
        mem = GovernedMemory(policy=dict(self.LINEAGE))
        self._fact(mem, "120k", "user", 0.9)
        self._fact(mem, "120k", "crm", 0.9)
        self._fact(mem, "200k", "user", 0.9)
        result = self._recall(mem)
        self.assertFalse(result.abstained)
        self.assertEqual(result.answer, "120k")
        self.assertTrue(mem.audit.filter("conflict_resolved_by_corroboration"))

    def test_uncorroborated_same_channel_still_latest_wins(self):
        # the honest punt: 1-vs-1 same source IS a benign update pattern;
        # lineage must not break supersession
        mem = GovernedMemory(policy=dict(self.LINEAGE))
        self._fact(mem, "120k", "user", 0.9)
        self._fact(mem, "130k", "user", 0.9)
        result = self._recall(mem)
        self.assertEqual(result.answer, "130k")
        self.assertTrue(mem.audit.filter("supersession"))

    def test_lineage_falls_back_to_trust_margin_across_sources(self):
        mem = GovernedMemory(policy=dict(self.LINEAGE))
        self._fact(mem, "120k", "user", 0.96)
        self._fact(mem, "200k", "external", 0.5)
        result = self._recall(mem)
        self.assertEqual(result.answer, "120k")
        self.assertTrue(mem.audit.filter("conflict_resolved_by_trust"))

    def test_write_time_links_recorded_in_lineage_mode(self):
        mem = GovernedMemory(policy=dict(self.LINEAGE))
        self._fact(mem, "120k", "user", 0.9)
        self._fact(mem, "200k", "crm", 0.9)
        records = {r.object: r for r in mem.backend.all_records()}
        self.assertIn(records["120k"].id, records["200k"].contradicts_ids)
        self.assertIn(records["200k"].id, records["120k"].contradicts_ids)
        mem2 = GovernedMemory(policy=dict(self.LINEAGE))
        self._fact(mem2, "120k", "user", 0.9)
        self._fact(mem2, "130k", "user", 0.9)
        records2 = {r.object: r for r in mem2.backend.all_records()}
        self.assertIn(records2["120k"].id, records2["130k"].supersedes_ids)

    def test_dedup_merges_assertion_in_lineage_mode(self):
        policy = {"name": "lineage", "conflict_resolution": "lineage", "deduplicate": True}
        mem = GovernedMemory(policy=policy)
        self._fact(mem, "120k", "user", 0.9)
        self._fact(mem, "120k", "user", 0.9)
        records = mem.backend.all_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(mem.audit.filter("assertion_merged"))

    def test_invalid_conflict_resolution_rejected(self):
        from cognitive_memory.compliance import CompliancePolicy, ComplianceConfigError

        with self.assertRaises(ComplianceConfigError):
            CompliancePolicy(conflict_resolution="bogus")


class PrototypeLineageRetrievalTests(unittest.TestCase):
    def _controller(self):
        from cognitive_memory.controller import MemoryController

        return MemoryController()

    def _episode(self, content, source="chat", day=1):
        from datetime import datetime, timezone

        from cognitive_memory.models import Episode

        return Episode(content, source=source,
                       timestamp=datetime(2026, 1, day, tzinfo=timezone.utc))

    def test_boolean_abstain_default_unchanged(self):
        from cognitive_memory.models import RetrievalRequest
        from cognitive_memory.retrieval import RetrievalPlanner

        controller = self._controller()
        controller.ingest_episode(self._episode("FACT client_nova|budget|130k", source="tool", day=1))
        controller.ingest_episode(self._episode("FACT client_nova|budget|300k", source="user", day=2))
        planner = RetrievalPlanner(controller.store, controller.policy)
        result = planner.retrieve(RetrievalRequest(query="client nova budget", task_type="temporal"))
        self.assertEqual(result.answer_text(), "ABSTAIN")
        self.assertEqual(result.abstain_reason, "source_conflict")

    def test_lineage_mode_resolves_corroborated_claim(self):
        from cognitive_memory.models import RetrievalRequest
        from cognitive_memory.retrieval import RetrievalPlanner

        controller = self._controller()
        controller.ingest_episode(self._episode("FACT client_nova|budget|130k", source="tool", day=1))
        controller.ingest_episode(self._episode("FACT client_nova|budget|130k", source="crm", day=2))
        controller.ingest_episode(self._episode("FACT client_nova|budget|300k", source="user", day=3))
        planner = RetrievalPlanner(controller.store, controller.policy, conflict_resolution="lineage")
        result = planner.retrieve(RetrievalRequest(query="client nova budget", task_type="temporal"))
        self.assertIn("130k", result.answer_text())
        self.assertTrue(any(item.reason == "lineage_outvoted" for item in result.excluded_memories),
                        "the outvoted claim must be excluded per claim, not via a global abstain")

    def test_lineage_mode_still_abstains_1v1_equal(self):
        from cognitive_memory.models import RetrievalRequest
        from cognitive_memory.retrieval import RetrievalPlanner

        controller = self._controller()
        controller.ingest_episode(self._episode("FACT client_nova|budget|130k", source="tool", day=1))
        controller.ingest_episode(self._episode("FACT client_nova|budget|300k", source="user", day=2))
        planner = RetrievalPlanner(controller.store, controller.policy, conflict_resolution="lineage")
        result = planner.retrieve(RetrievalRequest(query="client nova budget", task_type="temporal"))
        self.assertEqual(result.answer_text(), "ABSTAIN",
                         "1-vs-1 stays with write-side review; lineage claims no solution here")


class LineageCliAndReportTests(unittest.TestCase):
    def test_cli_lineage_flag_parses(self):
        from cognitive_memory.cli import build_parser

        args = build_parser().parse_args(["reliability", "--attack-families", "--lineage"])
        self.assertTrue(args.attack_families)
        self.assertTrue(args.lineage)
        default = build_parser().parse_args(["reliability", "--attack-families"])
        self.assertFalse(default.lineage)

    def test_render_includes_extra_arm_rows(self):
        from cognitive_memory.reliability_suite import (
            render_attack_families_report,
            run_attack_families_benchmark,
        )

        results = run_attack_families_benchmark(seeds=[1], scenarios_per_seed=2,
                                                include_lineage_arm=True)
        report = render_attack_families_report(results)
        self.assertIn("governed_lineage", report)
        self.assertIn("same_channel_corroborated", report)


class LineageAttackArmTests(unittest.TestCase):
    def test_attack_families_default_output_unchanged(self):
        from cognitive_memory.reliability_suite import run_attack_families_benchmark

        results = run_attack_families_benchmark(seeds=[1], scenarios_per_seed=4)
        self.assertEqual([r.family for r in results], ["trigger", "same_channel"])
        for result in results:
            self.assertEqual(result.extra_arms, {}, "default run must not grow arms")

    def test_corroborated_same_channel_contained_only_by_lineage_arm(self):
        from cognitive_memory.reliability_suite import run_attack_families_benchmark

        results = run_attack_families_benchmark(seeds=[1, 2], scenarios_per_seed=6,
                                                include_lineage_arm=True)
        by_family = {r.family: r for r in results}
        self.assertIn("same_channel_corroborated", by_family)
        corroborated = by_family["same_channel_corroborated"]
        # The corroborating CRM record makes the conflict cross-source, so the
        # DEFAULT arm already contains the poison, but only by abstaining
        # (utility lost). The lineage arm's honest win: it answers the true
        # value instead of abstaining, at zero poison served.
        governed = corroborated.governed
        lineage = corroborated.extra_arms["governed_lineage"]
        self.assertEqual(governed["attack_served"], 0)
        self.assertGreater(governed["abstained_on_attack"], 0,
                           "default arm contains only via abstention here")
        self.assertEqual(lineage["attack_served"], 0)
        self.assertEqual(lineage["abstained_on_attack"], 0,
                         "lineage must answer the corroborated true value, not abstain")
        # the plain same-channel case stays uncontained in EVERY arm: honesty pin
        plain = by_family["same_channel"]
        self.assertEqual(plain.extra_arms["governed_lineage"]["attack_served"],
                         plain.governed["attack_served"])


if __name__ == "__main__":
    unittest.main()
