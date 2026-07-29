import unittest
from datetime import datetime, timedelta, timezone

from cognitive_memory.reliability import GovernedMemory, Scope


def _clock_at(dt):
    return lambda: dt


class RetentionTests(unittest.TestCase):
    def _mem(self, base, **policy):
        pol = {"name": "r", "retention_days": {"high": 30, "restricted": 7}}
        pol.update(policy)
        return GovernedMemory(policy=pol, now_fn=_clock_at(base))

    def test_cleanup_removes_expired_keeps_fresh(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mem = self._mem(base)
        mem.remember("old fact here", subject="a", relation="note", object="x", tenant="t", entity="a")
        # advance the clock 40 days (> 30 day 'high' retention) and add a fresh one
        later = base + timedelta(days=40)
        mem._now_fn = _clock_at(later)
        mem.remember("new fact here", subject="b", relation="note", object="y", tenant="t", entity="b")
        removed = mem.cleanup_expired(now=later)
        self.assertEqual(removed, 1)  # only the 40-day-old record
        self.assertTrue(mem.recall_value("old fact", tenant="t", entity="a").abstained)  # deleted
        self.assertEqual(mem.recall_value("new fact", tenant="t", entity="b").answer, "y")  # kept

    def test_cleanup_audited(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mem = self._mem(base)
        mem.remember("old fact here", subject="a", relation="note", object="x", tenant="t", entity="a")
        mem.cleanup_expired(now=base + timedelta(days=40))
        self.assertTrue(mem.audit.filter("retention_cleanup"))
        self.assertTrue(mem.verify_audit())

    def test_no_retention_config_never_expires(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mem = GovernedMemory(policy={"name": "n"}, now_fn=_clock_at(base))  # no retention_days
        mem.remember("fact here now", subject="a", relation="note", object="x", tenant="t", entity="a")
        self.assertEqual(mem.cleanup_expired(now=base + timedelta(days=99999)), 0)

    def test_enforce_on_recall_hides_expired_before_cleanup(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mem = self._mem(base, enforce_retention_on_recall=True)
        mem.remember("old fact here", subject="a", relation="note", object="x", tenant="t", entity="a")
        # move clock past retention; record still stored but recall must hide it
        mem._now_fn = _clock_at(base + timedelta(days=40))
        result = mem.recall_value("old fact", tenant="t", entity="a")
        self.assertTrue(result.abstained)

    def test_created_at_is_set_on_write(self):
        base = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
        mem = GovernedMemory(now_fn=_clock_at(base))
        mem.remember("something here", subject="a", relation="note", object="x", tenant="t", entity="a")
        rec = [r for r in mem.backend.all_records() if r.subject == "a"][0]
        self.assertTrue(rec.created_at.startswith("2026-03-05"))


if __name__ == "__main__":
    unittest.main()
