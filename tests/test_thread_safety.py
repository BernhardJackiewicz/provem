import threading
import unittest

from cognitive_memory.mcp_server import GovernedMemoryService, ServerConfig
from cognitive_memory.reliability import GovernedMemory


class ConcurrentIngestTests(unittest.TestCase):
    def test_concurrent_ingest_no_lost_or_duplicate_records(self):
        mem = GovernedMemory()
        threads = 10
        per_thread = 100

        def worker(t):
            for i in range(per_thread):
                mem.remember("fact %d %d here" % (t, i), subject="s_%d_%d" % (t, i),
                             relation="note", object="v", tenant="t", entity="s_%d_%d" % (t, i))

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        records = mem.backend.all_records()
        self.assertEqual(len(records), threads * per_thread)  # none lost
        ids = [r.id for r in records]
        self.assertEqual(len(ids), len(set(ids)))  # no duplicate ids
        self.assertEqual(mem.clock, threads * per_thread)  # clock monotonic/consistent


class ConcurrentTenantTests(unittest.TestCase):
    def test_memory_for_single_instance_under_concurrency(self):
        service = GovernedMemoryService(ServerConfig())
        results = []

        def worker():
            results.append(id(service.memory_for("tenant_x")))

        ts = [threading.Thread(target=worker) for _ in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        # every thread must observe the same GovernedMemory instance
        self.assertEqual(len(set(results)), 1)


if __name__ == "__main__":
    unittest.main()
